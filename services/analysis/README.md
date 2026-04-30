# Stream of Worship - Analysis Service

The Analysis Service is a FastAPI-based microservice that performs CPU/GPU-intensive audio analysis and processing for the Stream of Worship platform.

## Features

- **Audio Analysis**: Detects tempo (BPM), musical key, mode, loudness, beats, and song sections
- **Stem Separation (Demucs)**: Separates audio into vocals, drums, bass, and other stems using Demucs
- **Clean Vocals Generation (Vocal Separation + UVR)**: Two-stage pipeline for high-quality vocal extraction with echo/reverb removal
- **LRC Generation**: Generates timestamped lyric files using Whisper + LLM alignment
- **R2 Storage**: Uploads/download results to/from Cloudflare R2 (S3-compatible)

## Architecture

This service is designed to run as a long-lived container with async job processing:

- **API Layer**: FastAPI endpoints for job submission and status checking
- **Job Queue**: In-memory queue with separate concurrency controls
  - Analysis jobs: Serialized (1 at a time) due to high memory/CPU usage with allin1
  - LRC jobs: Configurable concurrency (default: 2) with faster-whisper
- **Workers**: Background tasks for analysis, stem separation, and LRC generation
- **Cache**: Local filesystem cache for expensive operations

## Prerequisites

- Docker with Docker Compose
- For GPU support: NVIDIA Container Toolkit (`nvidia-container-toolkit`)
- Cloudflare R2 account (for audio storage)
- OpenAI-compatible API key (for LRC generation)

## Environment Variables

Create a `.env` file in this directory with the following variables:

### Required

```bash
# R2 Storage (Cloudflare R2 credentials)
SOW_R2_BUCKET="your-bucket-name"
SOW_R2_ENDPOINT_URL="https://<account-id>.r2.cloudflarestorage.com"
SOW_R2_ACCESS_KEY_ID="your-access-key"
SOW_R2_SECRET_ACCESS_KEY="your-secret-key"

# API Security (shared secret with Admin CLI)
SOW_ANALYSIS_API_KEY="your-random-api-key"

# Admin API Security (for job cancellation, clear queue)
SOW_ADMIN_API_KEY="your-secure-admin-key"  # Optional but recommended

# LLM Configuration (for LRC generation)
SOW_LLM_API_KEY="sk-or-v1-..."  # OpenRouter, OpenAI, etc.
SOW_LLM_BASE_URL="https://openrouter.ai/api/v1"
SOW_LLM_MODEL="openai/gpt-4o-mini"
```

### Optional

```bash
# Processing Configuration
SOW_MAX_CONCURRENT_ANALYSIS_JOBS=1  # Analysis jobs (default: 1, serialized for memory)
SOW_MAX_CONCURRENT_LRC_JOBS=2       # LRC jobs (default: 2, concurrent with faster-whisper)
SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS=1  # Stem separation jobs (default: 1, serialized)
SOW_DEMUCS_DEVICE=cpu               # "cpu" or "cuda" (default: cpu)
SOW_WHISPER_DEVICE=cpu              # "cpu" or "cuda" (default: cpu)

# Stem Separation Model Configuration
SOW_AUDIO_SEPARATOR_MODEL_ROOT="/path/to/audio-separator-models"  # Host path to pre-downloaded models
SOW_VOCAL_SEPARATION_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"  # Vocal separation model filename
SOW_DEREVERB_MODEL="UVR-De-Echo-Normal.pth"  # UVR-De-Echo model filename
```

## Quick Start

### 1. Build and Start the Service

```bash
cd services/analysis

# Build the image
docker compose build

# Start the service
docker compose up -d

# View logs
docker compose logs -f
```

The service will be available at `http://localhost:8000`.

### 2. Verify Health

```bash
curl http://localhost:8000/api/v1/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "services": {
    "r2": {"status": "configured", "bucket": "your-bucket"},
    "cache": {"status": "healthy", "path": "/cache"}
  }
}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info |
| `/api/v1/health` | GET | Health check |
| `/api/v1/jobs/analyze` | POST | Submit audio analysis job |
| `/api/v1/jobs/lrc` | POST | Submit LRC generation job |
| `/api/v1/jobs/stem-separation` | POST | Submit clean vocals stem separation job |
| `/api/v1/jobs/{job_id}` | GET | Get job status and results |
| `/api/v1/jobs/{job_id}/cancel` | POST | **(Admin)** Cancel a job |
| `/api/v1/jobs/clear-queue` | POST | **(Admin)** Cancel all queued jobs |

### Submit Analysis Job

```bash
curl -X POST http://localhost:8000/api/v1/jobs/analyze \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://your-bucket/hash/audio.mp3",
    "content_hash": "abc123...",
    "options": {
      "generate_stems": true,
      "stem_model": "htdemucs",
      "force": false
    }
  }'
```

### Submit LRC Generation Job

```bash
curl -X POST http://localhost:8000/api/v1/jobs/lrc \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://your-bucket/hash/audio.mp3",
    "content_hash": "abc123...",
    "lyrics_text": "Line 1\nLine 2\nLine 3",
    "options": {
      "whisper_model": "large-v3",
      "llm_model": "openai/gpt-4o-mini",
      "use_vocals_stem": true,
      "language": "zh"
    }
  }'
```

### Submit Stem Separation Job

Generates clean vocals and instrumental stems using a two-stage pipeline:
1. **Stage 1 (Vocal Separation)**: Extracts vocals from the mix
2. **Stage 2 (UVR-De-Echo)**: Removes echo/reverb from extracted vocals

```bash
curl -X POST http://localhost:8000/api/v1/jobs/stem-separation \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://your-bucket/hash/audio.mp3",
    "content_hash": "abc123...",
    "options": {
      "force": false,
      "dereverb_model": "UVR-De-Echo-Normal.pth"
    }
  }'
```

**Job Stages:**
- `checking_cache` - Checking for existing stems in R2
- `downloading` - Downloading source audio
- `stage1_vocal_separation` - Running MelBand Roformer vocal separation
- `stage2_dereverb` - Running UVR-De-Echo dereverberation
- `renaming_outputs` - Renaming outputs to canonical names
- `caching` - Caching results locally
- `uploading` - Uploading to R2
- `complete` - Job complete

**Output Files:**
- `{hash_prefix}/stems/vocals_clean.flac` - Clean vocals (no echo/reverb)
- `{hash_prefix}/stems/instrumental_clean.flac` - Instrumental accompaniment

**Auto-Trigger:** The LRC job automatically triggers stem separation when `use_vocals_stem=true` and no clean vocals exist. The LRC worker:
1. Checks for existing `vocals_clean.flac`
2. If not found, submits a child stem-separation job
3. Releases its concurrency slot while waiting
4. Re-acquires slot when child completes
5. Uses the clean vocals for Whisper transcription and passes URL to Qwen3

### Check Job Status

```bash
curl http://localhost:8000/api/v1/jobs/job_abc123 \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY"
```

## Stem Separation Model Setup

The stem separation feature requires pre-downloaded AI models on the host machine. Models are bind-mounted into the container as read-only.

### One-Time Model Download

Run this Python script on your host machine (outside Docker) to download the required models:

```python
# download_stem_models.py
from audio_separator.separator import Separator
import os

# Set cache directory for models
model_dir = os.path.expanduser("~/.cache/audio-separator")
os.makedirs(model_dir, exist_ok=True)

    print("Downloading MelBand Roformer model...")
    sep1 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
    sep1.load_model(model_filename="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt")

print("Downloading UVR-De-Echo model...")
sep2 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
sep2.load_model(model_filename="UVR-De-Echo-Normal.pth")

print(f"Models downloaded to: {model_dir}")
```

Or run directly:

```bash
python -c "
from audio_separator.separator import Separator
import os

model_dir = os.path.expanduser('~/.cache/audio-separator')
os.makedirs(model_dir, exist_ok=True)

sep1 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format='FLAC')
sep1.load_model(model_filename='model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt')

sep2 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format='FLAC')
sep2.load_model(model_filename='UVR-De-Echo-Normal.pth')

print(f'Models downloaded to: {model_dir}')
"
```

### Configure Environment

After downloading, set the environment variable:

```bash
# Add to your .env file
export SOW_AUDIO_SEPARATOR_MODEL_ROOT="$HOME/.cache/audio-separator"
```

The docker-compose.yml automatically mounts this directory to `/models/audio-separator:ro` in the container.

## Platform-Specific Builds

The Dockerfile supports platform-specific builds for PyTorch and NATTEN:

### x86_64 (Linux/AMD64) - CPU-only PyTorch

```bash
export TARGETPLATFORM=linux/amd64
docker compose build
```

### ARM64 (Apple Silicon) - Standard PyTorch

```bash
export TARGETPLATFORM=linux/arm64
docker compose build
```

## GPU Support

To enable GPU acceleration for Demucs and Whisper:

1. Install NVIDIA Container Toolkit:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install -y nvidia-container-toolkit
   sudo systemctl restart docker
   ```

2. Set environment variables:
   ```bash
   export SOW_DEMUCS_DEVICE=cuda
   export SOW_WHISPER_DEVICE=cuda
   ```

3. Uncomment the GPU section in `docker-compose.yml`:
   ```yaml
   deploy:
     resources:
       reservations:
         devices:
           - driver: nvidia
             count: 1
             capabilities: [ gpu ]
   ```

4. Start the service:
   ```bash
   docker compose up -d
   ```

If you don't have a GPU, the service will run fine on CPU with the default configuration.

## Stopping the Service

```bash
# Stop and remove containers
docker compose down

# Stop and remove containers + volumes (clears cache)
docker compose down -v
```

## Troubleshooting

### Service fails to start with "missing_credentials"

Check that all required R2 environment variables are set:
```bash
echo $SOW_R2_ACCESS_KEY_ID
echo $SOW_R2_SECRET_ACCESS_KEY
```

### LRC jobs fail with "SOW_LLM_API_KEY not set"

You must set all three LLM variables:
```bash
export SOW_LLM_API_KEY="your-key"
export SOW_LLM_BASE_URL="https://openrouter.ai/api/v1"
export SOW_LLM_MODEL="openai/gpt-4o-mini"
```

### GPU not detected

Verify NVIDIA Container Toolkit is installed:
```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

If this fails, GPU support is not properly configured.

### Container has no outbound internet access (DNS works, TCP times out)

If the container can resolve DNS but cannot establish TCP connections to external hosts (R2, GitHub, HuggingFace), this is typically caused by host firewall rules blocking Docker bridge traffic.

**Symptoms:**
- `Could not connect to the endpoint URL` errors when downloading audio from R2
- `Failed to load audio-separator models: HTTPSConnectionPool ... Network is unreachable` during first stem separation job (lazy initialization)
- LRC or stem separation jobs fail after hanging for several minutes

**Diagnosis:**
```bash
# Test outbound TCP from inside the container
docker compose exec analysis-dev python -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('1.1.1.1', 443))
    print('OK')
except Exception as e:
    print('Blocked:', e)
"
```

**Fix (choose one):**

1. **UFW** — allow Docker forwarding:
   ```bash
   sudo ufw allow in on docker0
   sudo ufw default allow FORWARD
   sudo ufw reload
   ```

2. **iptables** — add forwarding rules (replace `eth0` with your external interface):
   ```bash
   sudo iptables -A FORWARD -i docker0 -o eth0 -j ACCEPT
   sudo iptables -A FORWARD -i eth0 -o docker0 -m state --state ESTABLISHED,RELATED -j ACCEPT
   ```

3. **Quick workaround** — use host networking (no isolation):
   Add `network_mode: host` to the `analysis-dev` service in `docker-compose.yml`. Note: this removes container network isolation and port mapping is ignored.

### Models re-download every time / not found in cache directory

The `audio_separator.Separator` class stores downloaded model files in `model_file_dir`, **not** `output_dir`. If `model_file_dir` is not set, it defaults to `/tmp/audio-separator-models/` which is ephemeral.

**Fix:** Always pass `model_file_dir` when creating a `Separator` instance:
```python
sep = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
```

This applies to both the `start-dev.sh` download script and the `separator_wrapper.py` inside the container.

## Development

### Project Structure

```
services/analysis/
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # Multi-platform Docker build
├── pyproject.toml             # Python dependencies
├── README.md                  # This file
└── src/sow_analysis/
    ├── __init__.py
    ├── main.py                # FastAPI app entry point
    ├── config.py              # Pydantic settings
    ├── models.py              # Pydantic models
    ├── routes/                # API endpoints
    │   ├── health.py
    │   └── jobs.py
    ├── storage/               # R2 and cache clients
    │   ├── cache.py
    │   ├── db.py              # SQLite job persistence
    │   └── r2.py
    └── workers/               # Background job processors
        ├── analyzer.py
        ├── lrc.py
        ├── queue.py
        ├── separator.py       # Demucs stem separation
        ├── stem_separation.py # Vocal separation (MelBand Roformer) + UVR clean vocals
        └── separator_wrapper.py # AudioSeparator model management
```

### Running Tests

```bash
# From project root
pytest tests/services/analysis/ -v
```

## License

Part of the Stream of Worship project.
