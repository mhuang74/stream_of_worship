# Qwen3 Service - Production Deployment Guide

## Overview

The Qwen3 Alignment Service is a FastAPI microservice that provides forced alignment for LRC timestamp refinement. This guide covers deployment via Docker Compose for single-machine production environments.

The service uses the Qwen2-Audio-ForcedAlignment-0.6B model to align Whisper transcription phrases with provided lyrics, producing precise timestamps for each lyric line.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2.0+
- 4GB+ RAM available for GPU/MPS inference, 8GB+ for CPU-only inference
- Qwen3-ForcedAligner-0.6B model files downloaded (approx 1.2GB)

### Hardware Requirements

#### CPU-only inference:
- CPU: 4 cores recommended
- RAM: 8GB minimum
- Estimated alignment time: ~2-3 seconds per minute of audio
- Concurrency: 1-2 requests (keep low to avoid OOM)

#### GPU (NVIDIA CUDA / Apple Silicon MPS):
- GPU: 4GB VRAM minimum
- RAM: 4GB host RAM (8GB recommended for 2+ concurrent requests)
- Estimated alignment time: ~0.5-1 second per minute of audio
- Concurrency: 2-3 requests (adjust based on available VRAM)

## Model Setup

### 1. Download Qwen3-ForcedAligner-0.6B model

```bash
mkdir -p docker/models
cd docker/models
git lfs install
git clone https://huggingface.co/Qwen/Qwen2-Audio-ForcedAlignment-0.6B qwen3-forced-aligner
```

### 2. Verify model files

```bash
ls qwen3-forced-aligner/
# Should show: config.json, pytorch_model.bin, tokenizer.json, etc.
```

The model directory after download should contain:
- `config.json` - Model configuration
- `pytorch_model.bin` or `model.safetensors` - Model weights
- `tokenizer.json` - Tokenizer configuration
- `preprocessor_config.json` - Audio preprocessor configuration
- Other supporting files

## Configuration

### 1. Create environment file

Copy `.env.example` to `.env` and configure:

```bash
cd docker
cp ../services/qwen3/.env.example .env
# Edit .env with your configuration
```

### 2. Key configuration options

**Model Configuration:**

- `SOW_QWEN3_MODEL_PATH`: Path to model files (default: `/models/qwen3-forced-aligner`)
  - Use HuggingFace ID for auto-download: `Qwen/Qwen2-Audio-ForcedAlignment-0.6B`
  - Or use local path if manually downloaded

- `SOW_QWEN3_DEVICE`: Device to run on
  - `auto` (default): Auto-detect GPU or fallback to CPU
  - `cuda`: NVIDIA GPU (requires CUDA toolkit)
  - `mps`: Apple Silicon GPU
  - `cpu`: CPU inference (slower, no GPU required)

- `SOW_QWEN3_DTYPE`: Model precision
  - `float32` (default): Highest precision, all devices
  - `float16`: Faster on GPU, slight precision loss
  - `bfloat16`: Balanced performance/precision, GPU only

**Concurrency Settings:**

- `SOW_QWEN3_MAX_CONCURRENT`: Maximum concurrent alignment requests
  - `2` (default): Recommended for production with 8GB RAM
  - `3`: Higher throughput if 16GB+ RAM available
  - `1`: Conservative, minimal memory usage

**R2 Storage (Optional):**

Configure if using Cloudflare R2 for audio storage:

- `SOW_QWEN3_R2_BUCKET`: R2 bucket name
- `SOW_QWEN3_R2_ENDPOINT_URL`: R2 API endpoint URL
- `SOW_QWEN3_R2_ACCESS_KEY_ID`: R2 access key ID
- `SOW_QWEN3_R2_SECRET_ACCESS_KEY`: R2 secret access key

**API Security:**

- `SOW_QWEN3_API_KEY`: If set, all requests must include `X-API-Key` header with this value
  - Recommended for production
  - Leave empty for development (no authentication)

### 3. Environment variable reference

For complete documentation of all variables, see `services/qwen3/.env.example`.

## Deployment

### 1. Start the service

```bash
cd docker
docker-compose -f docker-compose.prod.yml up -d
```

This command:
- Builds the Docker image (if not already built)
- Creates and starts the container named `sow-qwen3`
- Maps host port 8000 to container port 8000
- Mounts model files and create cache volume
- Applies resource limits (4 CPUs, 8GB memory)
- Enables auto-restart (`unless-stopped`)

### 2. Monitor startup logs

```bash
docker-compose -f docker-compose.prod.yml logs -f qwen3
```

**Expected startup sequence:**

```
INFO:     Qwen3 Alignment Service starting up
INFO:     Loading Qwen3ForcedAligner model...
INFO:     Loading Qwen3ForcedAligner from /models/qwen3-forced-aligner on device=cuda, dtype=float32
INFO:     Qwen3ForcedAligner loaded and ready
INFO:     Qwen3 Alignment Service ready
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**Note:** Model loading may take 30-60 seconds on first startup. The health check has a 180-second `start_period` to accommodate this.

### 3. Verify health status

```bash
curl http://localhost:8000/health
```

**Expected response:**

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "model": "ready",
  "device": "cuda",
  "concurrency_limit": 2
}
```

If the service is still loading the model, the response will be:

```json
{
  "status": "starting",
  "version": "0.1.0",
  "model": "loading"
}
```

### 4. Test alignment endpoint

```bash
curl -X POST http://localhost:8000/api/v1/align \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/song.mp3",
    "lyrics": ["Line one", "Line two", "Line three"],
    "format": "lrc"
  }'
```

## Monitoring and Logging

### View logs

```bash
# Real-time logs
docker-compose -f docker-compose.prod.yml logs -f qwen3

# Last 100 lines
docker-compose -f docker-compose.prod.yml logs --tail=100 qwen3

# Logs with timestamps
docker-compose -f docker-compose.prod.yml logs -t qwen3
```

Log files are rotated automatically:
- Maximum file size: 10MB
- Maximum files retained: 3
- Total log space: ~30MB maximum

### Resource monitoring

```bash
docker stats sow-qwen3
```

Shows:
- CPU usage percentage
- Memory usage / limit
- Network I/O
- Block I/O

### Health check monitoring

The health check runs every 30 seconds with:
- Timeout: 10 seconds
- Retries: 3 consecutive failures mark container unhealthy
- Start period: 180 seconds (allows time for model loading)

Check health status:

```bash
docker inspect --format='{{.State.Health.Status}}' sow-qwen3
```

## Maintenance

### View container status

```bash
docker-compose -f docker-compose.prod.yml ps
```

### Restart the service

```bash
docker-compose -f docker-compose.prod.yml restart qwen3
```

### Stop the service

```bash
docker-compose -f docker-compose.prod.yml down
```

### Pull latest image updates

```bash
cd docker
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d
```

### Rebuild from source

```bash
cd docker
docker-compose -f docker-compose.prod.yml build --no-cache
docker-compose -f docker-compose.prod.yml up -d
```

### Clean up cache

The service uses a Docker volume `qwen3-cache` for temporary data. To clean:

```bash
docker-compose -f docker-compose.prod.yml down
docker volume rm docker_qwen3-cache
docker-compose -f docker-compose.prod.yml up -d
```

## Troubleshooting

### Service returns 503 on /health

**Symptoms:** Health check fails after initial startup period

**Possible causes:**
- Model failed to load
- Model files are corrupted or missing
- Device not available (e.g., CUDA not found)

**Debug steps:**

```bash
# Check logs for errors
docker-compose -f docker-compose.prod.yml logs qwen3

# Verify model path and files
docker exec sow-qwen3 ls -la /models/qwen3-forced-aligner/

# Check if device is available inside container
docker exec sow-qwen3 python -c "import torch; print(torch.cuda.is_available())"
```

**Common fixes:**
- Ensure model files are correctly downloaded
- Verify `SOW_QWEN3_DEVICE` setting matches hardware
- For GPU, ensure NVIDIA Container Toolkit is installed

### Out of memory errors

**Symptoms:** Container crashes, alignment requests fail with OOM errors

**Possible causes:**
- `MAX_CONCURRENT` too high for available memory
- Too many concurrent alignment requests
- Other services consuming memory

**Debug steps:**

```bash
# Check memory usage
docker stats sow-qwen3

# Check memory limit
docker inspect sow-qwen3 | memory
```

**Common fixes:**
- Reduce `SOW_QWEN3_MAX_CONCURRENT` to 1
- Change `SOW_QWEN3_DTYPE` to `float16` (may reduce precision)
- Close other memory-intensive services
- Increase `resources.limits.memory` in docker-compose.prod.yml

### Slow alignment performance

**Symptoms:** Alignment requests take longer than expected

**Possible causes:**
- Model running on CPU instead of GPU
- Disk I/O bottleneck
- Network congestion (for remote audio URLs)

**Debug steps:**

```bash
# Check what device is being used
docker-compose logs qwen3 | grep "device="

# Check disk I/O
iostat -x

# If GPU, verify GPU utilization
nvidia-smi  # NVIDIA GPUs
powermetrics --gpu  # Apple Silicon
```

**Common fixes:**
- Verify `SOW_QWEN3_DEVICE` is set to `auto`, `cuda`, or `mps`
- Ensure cache directory is on SSD (fast I/O)
- Increase concurrency if memory available
- For remote URLs, use R2 storage for faster downloads

### Container fails to start

**Symptoms:** Container exits immediately or during startup

**Debug steps:**

```bash
# Check exit code and logs
docker-compose -f docker-compose.prod.yml logs qwen3

# Check if port 8000 is already in use
lsof -i :8000

# Verify Docker image exists
docker images | grep sow-qwen3
```

**Common fixes:**
- Free up port 8000 or change port mapping
- Rebuild Docker image if corrupted
- Check environment syntax in .env file

## Security Considerations

- **Model files mounted read-only**: Model files are mounted with `:ro` to prevent accidental modification
- **API authentication**: Configure `SOW_QWEN3_API_KEY` to require authentication for all API requests
- **Network isolation**: Use private networks where possible to restrict access
- **Reverse proxy**: Consider running behind nginx for:
  - SSL/TLS termination
  - Additional authentication layers
  - Request/response caching
  - Rate limiting
- **Secrets management**: Use Docker secrets or environment variable managers for sensitive credentials (R2 keys, API keys)
- **Regular updates**: Pull latest image updates and rebuild periodically for security patches

## Performance Tuning

### Concurrency optimization

The optimal `MAX_CONCURRENT` setting depends on:

1. **Available VRAM** (GPU) or RAM (CPU):
   - 4GB VRAM/RAM: Use 1-2 concurrent requests
   - 8GB VRAM/RAM: Use 2-3 concurrent requests
   - 16GB+ VRAM/RAM: Use 3-4 concurrent requests

2. **Audio length**:
   - Short audio (< 2 min): Higher concurrency works well
   - Long audio (> 5 min): Lower concurrency to avoid OOM

3. **Throughput vs latency**:
   - Maximize throughput: Higher concurrency
   - Minimize latency: Lower concurrency per request

### Device selection

| Hardware | Recommended DEVICE | Recommended DTYPE |
|----------|-------------------|-------------------|
| NVIDIA GPU | cuda | bfloat16 (fastest) or float16 |
| Apple Silicon M1/M2 | mps | float32 (MPS doesn't fully support fp16/bf16) |
| CPU only | cpu | float32 |

### Resource limits

Adjust `deploy.resources` in `docker-compose.prod.yml` based on actual needs:

```yaml
deploy:
  resources:
    limits:
      cpus: "4.0"      # Adjust based on available CPU cores
      memory: 8G       # Adjust based on available RAM
    reservations:
      cpus: "2.0"      # Minimum guaranteed CPU
      memory: 4G       # Minimum guaranteed RAM
```

## Integration with Analysis Service

For integrated deployment with the Analysis Service, include qwen3 service in the main docker-compose.yml:

```yaml
services:
  # ... analysis service config ...
  analysis:
    # ... existing config ...
    environment:
      - SOW_ANALYSIS_QWEN3_URL=http://qwen3:8000

  qwen3:
    extends:
      file: docker-compose.prod.yml
      service: qwen3
    networks:
      - analysis-network

networks:
  analysis-network:
```

See `docs/analysis-integration.md` for full integration guide.

## API Reference

### Health Check

```
GET /health
```

Returns service status, version, and model readiness.

### Alignment Endpoint

```
POST /api/v1/align
Content-Type: application/json
```

Request body:
```json
{
  "audio_url": "https://example.com/song.mp3",
  "lyrics": ["Line one", "Line two", "Line three"],
  "format": "lrc"
}
```

Parameters:
- `audio_url`: URL to audio file (HTTP/HTTPS or s3:// for R2)
- `lyrics`: Array of lyric lines to align
- `format`: Output format - `lrc` (default) or `json`

Response (format=lrc):
```text
[00:01.50] Line one
[00:05.30] Line two
[00:09.10] Line three
```

Response (format=json):
```json
{
  "segments": [
    {
      "text": "Line one",
      "start": 1.5,
      "end": 5.2
    },
    {
      "text": "Line two",
      "start": 5.3,
      "end": 9.0
    }
  ]
}
```

## Additional Resources

- Qwen3 Forced Aligner: https://huggingface.co/Qwen/Qwen2-Audio-ForcedAlignment-0.6B
- Service Configuration: `services/qwen3/src/sow_qwen3/config.py`
- API Documentation: http://localhost:8000/docs (Swagger UI, when running)
- Analysis Service Integration: `services/analysis/src/sow_analysis/services/qwen3/`
