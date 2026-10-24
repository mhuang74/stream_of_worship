# Stream of Worship - Analysis Service

The Analysis Service is a FastAPI-based microservice that performs CPU/GPU-intensive audio analysis and processing for the Stream of Worship platform.

## Features

- **Audio Analysis**: Detects tempo (BPM), musical key, mode, loudness, beats, and song sections (all allin1 + Demucs + madmom work runs on Modal, not in this container)
- **Stem Separation (Demucs on Modal)**: Separates audio into vocals, drums, bass, and other stems
- **Clean Vocals Generation (Vocal Separation + UVR)**: Two-stage pipeline for high-quality vocal extraction with echo/reverb removal (MVSEP cloud API, failing over to the `sow-stems` Modal app)
- **LRC Generation**: Generates timestamped lyric files using Whisper + LLM alignment
- **R2 Storage**: Uploads/download results to/from Cloudflare R2 (S3-compatible)

## Architecture

This service is designed to run as a long-lived container with async job processing:

- **API Layer**: FastAPI endpoints for job submission and status checking
- **Job Queue**: In-memory queue with separate concurrency controls
  - Analysis jobs: capped at `SOW_ANALYZE_MAX_CONCURRENT` (default 2); the GPU work (allin1 + Demucs + madmom) runs on Modal (`sow-analyzer`, max_containers=2)
  - LRC jobs: Configurable concurrency (default: 2); Whisper transcription runs on Modal
- **Workers**: Background tasks for analysis, stem separation, and LRC generation
- **Cache**: Local filesystem cache for expensive operations

## Prerequisites

- Docker with Docker Compose
- Cloudflare R2 account (for audio storage)
- OpenAI-compatible API key (for LRC generation)
- Modal account with `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` (all ML runs on Modal)

## Environment Variables

The active `.env` file is loaded from the centralized location `/opt/sow/.env` (not from this directory). A `.env.example` file is provided in this directory as a template — copy and configure it:

```bash
cp ops/analysis-service/.env.example /opt/sow/.env
```

The following variables are required:

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

# Embedding Configuration (for song/line embedding generation)
SOW_EMBEDDING_API_KEY="sk-..."  # OpenAI-compatible embedding provider
SOW_EMBEDDING_BASE_URL="https://api.openai.com/v1"
SOW_EMBEDDING_MODEL="text-embedding-3-small"
```

### Optional

```bash
# Processing Configuration
SOW_ANALYZE_MAX_CONCURRENT=2  # Max concurrent full-analyze jobs (default: 2)

# Whisper runs on Modal (see "Whisper on Modal" below); fill these in on the server
MODAL_TOKEN_ID=""
MODAL_TOKEN_SECRET=""
SOW_MODAL_WHISPER_ALLOWED_MODELS="tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3"
SOW_WHISPER_CACHE_VERSION=2

# BPM Algorithm (used by Fast Analysis jobs)
BPM_ALGORITHM_VERSION="v4_octave_guard"  # v4_octave_guard (default) or v5_cps_prior

# Stem separation runs on Modal (see "Stem separation on Modal" below)
SOW_VOCAL_SEPARATION_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"  # Vocal separation model filename
SOW_DEREVERB_MODEL="UVR-De-Echo-Normal.pth"  # UVR-De-Echo model filename
SOW_MODAL_STEM_MAX_ATTEMPTS=2
SOW_MODAL_STEM_MAX_AUDIO_BYTES=100000000

# Full analysis runs on Modal (sow-analyzer)
SOW_MODAL_ANALYZER_APP_NAME="sow-analyzer"
SOW_MODAL_ANALYZER_CLASS_NAME="SongAnalyzer"
SOW_MODAL_ANALYZER_MAX_ATTEMPTS=2
SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES=150000000
SOW_ANALYSIS_CACHE_VERSION=2

# DashScope Qwen3 ASR (Optional — improves LRC transcription quality)
SOW_DASHSCOPE_API_KEY=""  # DashScope API key; leave empty to skip Qwen3 ASR (uses Whisper only)
SOW_DASHSCOPE_ASR_REGION="intl"  # Region: intl, cn, or us
```

## Quick Start

### 1. Build and Start the Service

```bash
cd ops/analysis-service

# Build the image
docker compose build

# Start the service
docker compose up -d

# View logs
docker compose logs -f
```

The service will be available at `http://localhost:8000`.

### 2. Deploy the Modal Apps

Full analysis (plus Whisper, forced alignment, stem-separation failover) runs on Modal.
Deploy once with `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` exported — see the per-app
sections below:

```bash
uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/analyzer_app.py
```

### 3. Verify Health

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
- `mvsep_stage1_*` / `stage1_vocal_separation` - Vocal separation (MVSEP, or Modal directly when MVSEP is unavailable)
- `mvsep_stage2_*` / `stage2_dereverb` - De-reverb (MVSEP, or Modal)
- `fallback_modal` - MVSEP Stage 1 exhausted its retries; the whole pipeline runs on Modal
- `fallback_modal_stage2` - MVSEP Stage 1 succeeded but Stage 2 exhausted its retries; de-reverb runs on Modal
- `renaming_outputs` - Renaming outputs to canonical names
- `caching` - Caching results locally
- `uploading` - Uploading to R2
- `complete` - Job complete

**Output Files:**
- `{hash_prefix}/stems/vocals_dry.flac` - Dry vocals (no echo/reverb, Stage 2 output)
- `{hash_prefix}/stems/vocals.flac` - Vocals with reverb (Stage 1 output)
- `{hash_prefix}/stems/instrumental.flac` - Instrumental accompaniment

**Auto-Trigger:** The LRC job automatically triggers stem separation when `use_vocals_stem=true` and no dry vocals exist. The LRC worker:
1. Checks for existing `vocals_dry.flac` (with fallback to legacy `vocals_clean.flac`)
2. If not found, submits a child stem-separation job
3. Releases its concurrency slot while waiting
4. Re-acquires slot when child completes
5. Uses the dry vocals for Whisper transcription and passes URL to the forced aligner

### Check Job Status

```bash
curl http://localhost:8000/api/v1/jobs/job_abc123 \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY"
```

## Stem separation on Modal

Stem separation (the `stem_separation` job type and the LRC job's vocal-stem
prerequisite) runs on [Modal](https://modal.com), not in this container:

1. **MVSEP cloud API** is the primary backend when `SOW_MVSEP_API_KEY` is set
   (two-stage: vocal separation, then UVR-De-Echo).
2. **sow-stems** (Modal, `modal_apps/stem_separator_app.py`) is the failover — it
   runs `audio-separator` with the same two models on a GPU. There is **no in-container
   fallback by design**: the container is CPU-only and the audio-separator stack (plus
   its ONNX runtime and model files) was dropped from `pyproject.toml`.

Failover is per stage: if Stage 1 exhausts its retries, the whole two-stage pipeline
reruns on Modal (`fallback_modal`). If only Stage 2 (de-reverb) exhausts its retries,
MVSEP's Stage 1 vocals are re-de-reverbed on Modal (`fallback_modal_stage2`). A
non-retryable MVSEP classification error fails the job instead — a parsing bug is not
an infrastructure outage.

One-time setup:

1. Deploy the app (from `ops/analysis-service/`, with your Modal token exported — this
   also creates the `sow-stems-models` volume that caches the two model files):

   ```bash
   uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/stem_separator_app.py
   ```

2. Put the token in `/opt/sow/.env` (shared with Whisper and the forced aligner):

   ```bash
   MODAL_TOKEN_ID="ak-..."
   MODAL_TOKEN_SECRET="as-..."
   ```

The two model filenames are passed to the Modal class as parameters
(`SOW_VOCAL_SEPARATION_MODEL`, `SOW_DEREVERB_MODEL`), so changing a model means a new
Modal class handle — not an image rebuild.

Tunable knobs (all optional, defaults shown):

```bash
SOW_MODAL_STEM_APP_NAME="sow-stems"
SOW_MODAL_STEM_CLASS_NAME="StemSeparator"
SOW_MODAL_STEM_MAX_ATTEMPTS=2
SOW_MODAL_STEM_RETRY_DELAY_SECONDS=5.0
SOW_MODAL_STEM_MAX_AUDIO_BYTES=100000000
SOW_VOCAL_SEPARATION_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
SOW_DEREVERB_MODEL="UVR-De-Echo-Normal.pth"
```

- Retries are owned by the client; the Modal class deliberately sets no `retries=`.
- `SOW_MODAL_STEM_MAX_AUDIO_BYTES` caps the uploaded audio. Stage 2 uploads the
  lossless vocals FLAC (larger than the original MP3), hence the higher cap than
  Whisper's/aligner's.
- Concurrency is bounded by the app (`max_containers=2`).
- Waiting for MVSEP free-tier quota is unconditional (no `SOW_FREE_ONLY_MODE` any
  more): the job waits for the daily reset and then runs on MVSEP, failover to Modal
  only happens once there is nothing left to wait for.

Troubleshooting: container-side failures only appear in Modal's logs:

```bash
uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-stems
```

The analysis-service error message contains a `request_id` — grep `modal app logs`
for it to find the matching `[sow-stems][<id>]` traceback.

## Forced alignment on Modal

Forced alignment (the `forced_alignment` job type) runs on [Modal](https://modal.com) as
the **sow-forced-aligner** app, not in this container — the container is CPU-only, so an
in-process Qwen3-ForcedAligner would serialize every alignment on CPU.

The deployed app is **required at runtime**: the job fails with a configuration error
until `sow-forced-aligner` is deployed and the Modal tokens are set. There is no local
fallback by design.

One-time setup:

1. Deploy the app (from `ops/analysis-service/`, with your Modal token exported — this
   also creates the `sow-forced-aligner-models` volume that caches the ~1.2 GB checkpoint):

   ```bash
   uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/forced_aligner_app.py
   ```

2. Put the token in `/opt/sow/.env` (shared with Whisper):

   ```bash
   MODAL_TOKEN_ID="ak-..."
   MODAL_TOKEN_SECRET="as-..."
   ```

The client sends the resolved audio (vocal stem or original) as 16 kHz mono PCM-16 WAV —
the input rate of the aligner — so one upload is ~9.6 MB per 300 s of audio. Recordings
over the model limit are uploaded as one such chunk at a time rather than whole.

Tunable knobs (all optional, defaults shown):

```bash
SOW_MODAL_FORCED_ALIGNER_APP_NAME="sow-forced-aligner"
SOW_MODAL_FORCED_ALIGNER_CLASS_NAME="ForcedAligner"
SOW_MODAL_FORCED_ALIGNER_MAX_ATTEMPTS=2
SOW_MODAL_FORCED_ALIGNER_RETRY_DELAY_SECONDS=5.0
SOW_MODAL_FORCED_ALIGNER_MAX_AUDIO_BYTES=90000000
SOW_FORCED_ALIGN_MAX_CHUNK_SECONDS=300.0
SOW_FORCED_ALIGN_MAX_TOTAL_SECONDS=900.0
SOW_FORCED_ALIGN_CUT_SEARCH_SECONDS=30.0
```

- Retries are owned by the client; the Modal class deliberately sets no `retries=`, so a
  crashing input cannot be executed N×M times on the GPU.
- `SOW_MODAL_FORCED_ALIGNER_MAX_AUDIO_BYTES` caps the WAV upload; `validate_audio_duration`
  rejects inputs longer than `SOW_FORCED_ALIGN_MAX_TOTAL_SECONDS`, so the default is
  headroom, not a real limit.
- Concurrency is bounded by the app (`max_containers=2`).

Long recordings (300–900 s) cannot be aligned in one pass — the model tops out at five
minutes. Those jobs are chunked: `find_chunk_boundaries` splits the audio into
`ceil(duration / (300 − 30))` pieces, snapping each ideal cut to the quietest RMS frame
within ±`SOW_FORCED_ALIGN_CUT_SEARCH_SECONDS` (an unreadable file falls back to evenly
spaced cuts), `assign_lines_to_chunks` hands each chunk the lyric lines whose timestamps
fall inside it, each chunk is aligned separately, and the per-chunk `(start, end)` values
are shifted by the chunk offset before the LRC is built. There is no separate LRC
overlap/dedup step: the final LRC only uses line start timestamps.

```
Forced alignment chunking job_abc: duration=610.0s boundaries=[0.0, 203.3, 406.7, 610.0] chunks=3 lines_per_chunk=[12, 13, 11] lrc_times=True
Forced alignment chunk 2/3: 203.3s-406.7s lines=13 segments=241
```

A failing chunk fails the job with the chunk index in the message (`chunk 2/3: …`), so the
operator can tell a model failure from a bad cut.

Every call logs one line with the total Modal time (it includes cold start and the audio
upload, and starts after the local transcode):

```
Forced alignment total Modal elapsed time: 3.41s (attempts=1/2, segments=57, request_id=9f2c…; includes cold start and audio upload)
```

Troubleshooting: container-side failures (cold start, CUDA OOM, model load) are only
visible in Modal's logs:

```bash
uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-forced-aligner
```

For a failed alignment, the analysis-service error message contains a `request_id` —
grep `modal app logs` for that id to find the matching `[sow-forced-aligner][<id>]`
traceback.

## Audio LRC Alignment CLI

You can also run forced alignment directly from the CLI without submitting an API job:

```bash
# Align lyrics to audio using the forced aligner
uv run --extra app sow-admin lyrics align --audio <audio-file> --lyrics <lyrics-text> --output <output.lrc>
```

## DashScope Qwen3 ASR Setup (Optional)

The LRC generation pipeline uses DashScope Qwen3 ASR for transcription before Whisper fallback. When `SOW_DASHSCOPE_API_KEY` is set, Qwen3 ASR is called first; if it fails, Whisper is used as a fallback.

### Get an API Key

1. Visit [DashScope Console](https://dashscope.console.aliyun.com/)
2. Create an API key for your account
3. Add to `/opt/sow/.env`:

```bash
SOW_DASHSCOPE_API_KEY="your-dashscope-api-key"
```

### How It Works

The LRC worker's transcription pipeline:

1. **Qwen3 ASR** — Direct transcription via `qwen3-asr-flash` model (for audio < ~10MB or short duration)
2. **Qwen3 Filetrans** — File upload transcription via `qwen3-asr-flash-filetrans` model (for larger audio)
3. **Whisper on Modal** — Fallback if both Qwen3 methods fail (runs in the `sow-whisper` Modal app, not locally)

Results are cached locally to avoid redundant API calls. Cache version (`SOW_DASHSCOPE_ASR_CACHE_VERSION`) can be bumped to invalidate old cache entries.

### Tuning

| Env Var | Default | Description |
|---------|---------|-------------|
| `SOW_DASHSCOPE_ASR_TIMEOUT_SECONDS` | `300` | Timeout for direct ASR calls |
| `SOW_DASHSCOPE_ASR_FILETRANS_TIMEOUT_SECONDS` | `1800` | Timeout for filetrans ASR calls |
| `SOW_DASHSCOPE_ASR_MAX_CONCURRENT` | `2` | Max concurrent ASR requests |
| `SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS` | `10000` | Max context chars for LLM alignment |

## Whisper on Modal

Whisper transcription runs on [Modal](https://modal.com), not in this container — the
container stays CPU-only and no longer loads a Whisper model (the old in-container
path could reach ~12 GiB RAM and trigger earlyoom).

The deployed app is **required at runtime**: every LRC job that falls back to Whisper
fails with a configuration error until `sow-whisper` is deployed and the Modal tokens
are set. There is no local Whisper fallback by design.

One-time setup:

1. Deploy the app (from `ops/analysis-service/`, with your Modal token exported):

   ```bash
   uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/whisper_app.py
   ```

2. Put the token in `/opt/sow/.env`:

   ```bash
   MODAL_TOKEN_ID="ak-..."
   MODAL_TOKEN_SECRET="as-..."
   ```

Tunable knobs (all optional, defaults shown):

```bash
SOW_MODAL_WHISPER_APP_NAME="sow-whisper"
SOW_MODAL_WHISPER_CLASS_NAME="WhisperTranscriber"
SOW_MODAL_WHISPER_MAX_ATTEMPTS=2
SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS=5.0
SOW_MODAL_WHISPER_MAX_AUDIO_BYTES=90000000
SOW_MODAL_WHISPER_ALLOWED_MODELS="tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3"
SOW_WHISPER_CACHE_VERSION=2
```

- `SOW_MODAL_WHISPER_ALLOWED_MODELS` gates `options.whisper_model` per request; a model
  outside the list fails the job before any Modal call. Adjust it to the models you are
  willing to pay GPU time for (they are downloaded from Hugging Face on first use and
  cached in the `sow-whisper-models` Modal volume).
- `SOW_WHISPER_CACHE_VERSION` invalidates cached Whisper transcriptions; bump it if you
  change the Whisper backend in a way that changes outputs.

Troubleshooting: container-side errors (cold-start/model-load failures, CUDA OOM,
cuDNN problems) are only visible in Modal's logs — client logs cannot distinguish
model-load failure from transient infra:

```bash
uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-whisper
```

For a failed transcription, the analysis-service log's error message contains a
`request_id` — grep `modal app logs` for that id to find the matching container-side
traceback.

## Component Analysis (v5)

The analysis service performs chorus/verse component extraction via the
`component-analysis` job endpoint. The worker (`workers/components.py`) uses
cached allin1 sections or LRC lyrics repetition to identify components, then
computes per-component features (BPM, key, groove, backbeat, energy,
confidence).

### v5 Options (opt-in)

The v5 pipeline adds rich per-component metadata (theme, vocal posture,
per-field confidence, energy-aware roles, downbeat snapping). All v5 options
are **opt-in** — when none are specified, the stable v3 path runs unchanged.

| Option | Cost |
|--------|------|
| `use_stems` | **Heavy ML (Modal).** Triggers Demucs stem separation on the Modal GPU (storage per song). |
| `classify_theme` | **LLM cost.** One LLM call per component (12 Chinese themes). Requires `SOW_LLM_*` env vars. |
| `classify_vocal_posture` | **LLM cost.** One LLM call per component (3 posture categories). Requires `SOW_LLM_*` env vars. |
| `snap_to_downbeat` | **NN inference.** Runs madmom two-stage RNN downbeat detector if downbeats not cached. Falls back to beat-only on failure. |
| `energy_aware_roles` | **Compute.** Energy-based role assignment using drums stem (`0.4×rms + 0.3×drums_onset + 0.3×backbeat`). Best combined with `use_stems`. |

These options are submitted by the admin CLI (`sow-admin audio components`)
and passed through in the job payload `options` dict. See the
[admin CLI README](../../ops/admin-cli/README.md#audio-components) for CLI usage
and `reports/chorus_component_metadata_v5_impl_summary.md` for implementation
details.

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
- `Failed to load audio-separator models: HTTPSConnectionPool ... Network is unreachable` in Modal logs for `sow-stems` (the container itself no longer loads these models)
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

### Stem separation fails with a Modal configuration error

Stem separation has no in-container backend: the container image does not ship
`audio-separator`, its ONNX runtime, or the model files. The job fails with
`ModalStemSeparatorConfigError` (message mentions `MODAL_TOKEN_ID` or the deploy
command) until:

1. `sow-stems` is deployed (`modal deploy modal_apps/stem_separator_app.py`), and
2. `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are set for the container.

Model files are downloaded by the Modal container on first use into the
`sow-stems-models` volume, so the first job is slow and later ones are not.

### Models re-download every time (other backends)

All models live in Modal volumes (`sow-whisper-models`, `sow-forced-aligner-*`,
`sow-stems-models`, `sow-analyzer-models`); nothing model-sized is cached in the
container's `/cache` volume.

## Development

### Project Structure

```
ops/analysis-service/
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # Container build (librosa-only; no torch/natten)
├── pyproject.toml             # Python dependencies
├── README.md                  # This file
├── modal_apps/                # GPU work hosted on Modal (deployed separately)
│   ├── whisper_app.py         # sow-whisper: faster-whisper transcription
│   ├── forced_aligner_app.py  # sow-forced-aligner: Qwen3-ForcedAligner
│   ├── stem_separator_app.py  # sow-stems: audio-separator (MVSEP failover)
│   └── analyzer_app.py        # sow-analyzer: allin1 + Demucs + madmom full analysis
└── src/sow_analysis/
    ├── __init__.py
    ├── main.py                # FastAPI app entry point
    ├── config.py              # Pydantic settings
    ├── models.py              # Pydantic models
    ├── routes/                # API endpoints
    │   ├── health.py
    │   └── jobs.py
    ├── services/              # External backends (Modal, MVSEP, DashScope, LLM)
    │   ├── modal_analyzer_client.py
    │   ├── modal_stem_separator_client.py
    │   └── ...
    ├── storage/               # R2 and cache clients
    │   ├── cache.py
    │   ├── db.py              # SQLite job persistence
    │   └── r2.py
    └── workers/               # Background job processors
        ├── analyzer.py        # Modal orchestration + cache for full analysis
        ├── lrc.py
        ├── queue.py
        └── stem_separation.py # MVSEP pipeline, Modal failover, canonical names
```

### Running Tests

```bash
# From project root
pytest tests/ops/analysis-service/ -v
```

## License

Part of the Stream of Worship project.
