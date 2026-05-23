# Stream of Worship — Render Worker

AWS Lambda container that processes render jobs from an SQS queue. Ported from the Node.js render pipeline in the web app, this Python worker handles audio mixing, lyrics video encoding, and artifact upload to R2.

## Architecture

```
Next.js POST /api/render-jobs
  → Create DB job (status: queued)
  → SQS sendMessage({ jobId, songsetId, userId })
  → Lambda handler picks up message
  → execute_render_pipeline()
      1. preparing   — fetch songset items from DB
      2. mixing_audio — FFmpeg audio concatenation with gaps/crossfade/loudnorm
      3. rendering_frames — Pillow frame rendering with CJK lyrics
      4. encoding_video — FFmpeg video encoding from raw RGBA frames
      5. uploading — R2 upload of MP3/MP4/chapters.json
  → Update DB job (status: completed or failed)
```

## How the Render Worker Is Triggered from Next.js

The render worker is invoked via SQS when a user submits a render request through the web app. The full dispatch flow is:

### 1. User Request

The browser sends `POST /api/render-jobs` with a payload like:

```json
{
  "songsetId": "songset-1",
  "template": "default",
  "resolution": "1080p",
  "audioEnabled": true,
  "videoEnabled": true,
  "fontSizePreset": "medium",
  "includeTitleCard": true,
  "titleCardDurationSeconds": 5
}
```

### 2. Next.js API Route (`webapp/src/app/api/render-jobs/route.ts`)

1. **Auth check** — validates session via `auth.api.getSession()`
2. **Input validation** — Zod schema (`createRenderJobSchema`) validates the request body
3. **Create DB record** — `createRenderJob()` inserts a row into `render_jobs` with `status: "queued"`, `phase: "preparing"`
4. **Enqueue to SQS** — sends a minimal message containing only `{ jobId, songsetId, userId }`; the Lambda worker fetches all other details from the database
5. **Error handling** — if SQS send fails, calls `failRenderJob()` to mark the DB record as failed, then returns HTTP 500

### 3. SQS Queue (`sow-render-jobs`)

- **Visibility timeout**: 900s (15 min) — must exceed max render duration
- **maxReceiveCount**: 3 — after 3 failures, message moves to the DLQ (`sow-render-jobs-dlq`)
- **Lambda event-source-mapping**: batch-size 1 (one render per invocation)

### 4. Lambda Handler (`lambda_handler.py`)

1. Parses `event["Records"]` array, extracts `body` JSON
2. Validates `jobId` and `userId` fields are present
3. Calls `execute_render_pipeline(job_id, user_id, conn)`
4. Returns `batchItemFailures` for any failed records, causing SQS to retry those messages

### 5. Render Pipeline (`pipeline.py`)

The 5-phase orchestrator runs:

1. **preparing** — fetch songset items from DB, estimate render time
2. **mixing_audio** — FFmpeg audio concatenation with gaps/crossfade/loudnorm
3. **rendering_frames** — Pillow frame rendering with CJK lyrics
4. **encoding_video** — FFmpeg video encoding from raw RGBA frames
5. **uploading** — R2 upload of MP3/MP4/chapters.json

After completion, `complete_render_job()` sets `status: "completed"` and stores R2 keys. On failure, `fail_render_job()` sets `status: "failed"` with an error message.

### 6. Progress Tracking (Pull-Based)

There is **no push callback** from Lambda to the webapp. Instead:

- The Lambda worker updates the shared PostgreSQL database directly at each phase transition
- The browser subscribes to `GET /api/render-jobs/[id]/events` (SSE endpoint in `webapp/src/app/api/render-jobs/[id]/events/route.ts`)
- The SSE endpoint polls the DB every 1 second and streams updates to the client
- Terminal states (completed/failed/cancelled) close the SSE stream
- Max SSE duration: 30 minutes

### 7. Orphan Recovery

Both the webapp (`recoverOrphanedJobs()` in `webapp/src/lib/render/job-manager.ts`) and the Lambda worker (`recover_orphaned_jobs()` in `db.py`) independently detect jobs stuck in `running` for over 30 minutes and mark them as `failed`.

### End-to-End Diagram

```
Browser
  │
  │ POST /api/render-jobs { songsetId, template, resolution, ... }
  ▼
Next.js (Vercel)
  │ 1. auth.api.getSession() — verify user
  │ 2. createRenderJob() — INSERT render_jobs (status: "queued")
  │ 3. SQSClient.sendMessage({ jobId, songsetId, userId })
  │       │
  │       ▼
  │   AWS SQS (sow-render-jobs)
  │   (VisibilityTimeout: 900s, maxReceiveCount: 3, DLQ: sow-render-jobs-dlq)
  │
  │ GET /api/render-jobs/[id]/events  (SSE stream)
  │       │
  │       ▼
  │   Poll DB every 1s → stream SSE events to client
  │
  ▼
AWS Lambda (sow-render-worker)
  │ (triggered by SQS event-source-mapping, batch-size: 1)
  │
  │ handler(event) — parse SQS Records
  │ _process_record() — extract jobId, userId from body
  │
  │ execute_render_pipeline(job_id, user_id, conn)
  │   │ start_render_job() — atomic claim (queued → running)
  │   │ Phase 1: preparing — fetch songset items, estimate time
  │   │ Phase 2: mixing_audio — FFmpeg audio mix
  │   │ Phase 3: rendering_frames — Pillow frame rendering
  │   │ Phase 4: encoding_video — FFmpeg video encoding
  │   │ Phase 5: uploading — R2 upload (MP3, MP4, chapters.json)
  │   │ complete_render_job() — DB: status = "completed", store R2 keys
  │   │ OR fail_render_job() — DB: status = "failed", error_message
  │
  ▼
PostgreSQL (Neon) — shared DB, updated by both webapp and Lambda
Cloudflare R2 — stores rendered artifacts
```

## Prerequisites

- Python 3.11
- Docker (for containerized builds)
- FFmpeg and CJK fonts (installed in the Docker image)
- AWS account with SQS, Lambda, and private ECR access
- Cloudflare R2 account
- Neon PostgreSQL database

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SOW_DATABASE_URL` | Neon PostgreSQL connection string |
| `SOW_R2_BUCKET` | Cloudflare R2 bucket name |
| `SOW_R2_ENDPOINT_URL` | R2 S3-compatible endpoint (`https://<account-id>.r2.cloudflarestorage.com`) |
| `SOW_R2_ACCESS_KEY_ID` | R2 access key ID |
| `SOW_R2_SECRET_ACCESS_KEY` | R2 secret access key |
| `SOW_AWS_REGION` | AWS region for SQS and Lambda (default: `us-west-2`) |
| `SOW_SQS_QUEUE_URL` | SQS queue URL for render job messages |

Copy `.env.example` to `.env` and fill in the values for local development.

## Local Development

### Install Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-mock pytest-asyncio
```

### Run Tests

```bash
PYTHONPATH=src pytest tests/ -v
```

### Run a Single Test File

```bash
PYTHONPATH=src pytest tests/test_pipeline.py -v
```

## Local Testing with Docker

The Docker Compose setup runs the Lambda container locally with the Lambda Runtime Interface Emulator (RIE) on port 9000.

### Build and Start

```bash
docker compose up --build
```

### Send a Test Event

```bash
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d '{
    "Records": [{
      "messageId": "test-1",
      "body": "{\"jobId\": \"your-job-id\", \"songsetId\": \"your-songset-id\", \"userId\": 1}"
    }]
  }'
```

### Stop

```bash
docker compose down
```

## Module Reference

| Module | Description |
|--------|-------------|
| `lambda_handler` | SQS event parsing, job dispatch, batch failure handling |
| `config` | Environment variable loading with validation |
| `pipeline` | 5-phase render orchestrator with cancellation and progress |
| `audio_engine` | FFmpeg audio mixing with gap, crossfade, and loudnorm |
| `video_engine` | FFmpeg video encoding from Pillow-rendered frames |
| `frame_renderer` | Pillow-based lyrics frame rendering with CJK fonts |
| `chapters` | Chapter manifest generation and FFFMETADATA1 output |
| `lrc_parser` | LRC timestamp parsing and global timeline conversion |
| `r2_client` | boto3 S3-compatible client for Cloudflare R2 |
| `asset_fetcher` | R2 download with local filesystem cache |
| `uploader` | R2 upload of MP3/MP4/chapters artifacts |
| `db` | psycopg2 job status CRUD (start, progress, complete, fail, recover orphans) |

## Deployment

### Manual Deployment

1. **Build the Docker image:**
   ```bash
   docker build -t sow-render-worker .
   ```

2. **Push to AWS ECR:**
   ```bash
   aws ecr get-login-password --region us-west-2 | \
     docker login --username AWS --password-stdin \
     762288208920.dkr.ecr.us-west-2.amazonaws.com

   docker tag sow-render-worker:latest \
     762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest

   docker push 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
   ```

3. **Update the Lambda function:**
   ```bash
   aws lambda update-function-code \
     --function-name sow-render-worker \
     --image-uri 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
   ```

> **Note:** AWS Lambda requires container images to be hosted in a **private ECR repository**. Public ECR image URIs are not accepted when creating or updating a Lambda function.

### Automated Deployment (GitHub Actions)

Pushes to `main` that modify `services/render-worker/` trigger the deploy workflow in `.github/workflows/deploy.yml`, which:

1. Configures AWS credentials from repository secrets
2. Logs in to private ECR
3. Builds, tags, and pushes the Docker image
4. Updates the Lambda function code with the new image URI

Required GitHub secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

### SQS Queue Setup

Create the SQS queue and dead-letter queue in AWS:

1. **Create the DLQ:**
   ```bash
   aws sqs create-queue --queue-name sow-render-jobs-dlq
   ```

2. **Create the main queue** with redrive policy and visibility timeout:
   ```bash
   aws sqs create-queue --queue-name sow-render-jobs \
     --attributes '{
       "RedrivePolicy": "{\"deadLetterTargetArn\":\"arn:aws:sqs:us-west-2:762288208920:sow-render-jobs-dlq\",\"maxReceiveCount\":\"3\"}",
       "VisibilityTimeout": "900"
     }'
   ```

   - Visibility timeout: 900s (15 min) — must exceed max render duration
   - maxReceiveCount: 3 — after 3 failures, message moves to DLQ

3. **Connect Lambda to SQS:**
   ```bash
   aws lambda create-event-source-mapping \
     --function-name sow-render-worker \
     --batch-size 1 \
     --event-source-arn arn:aws:sqs:us-west-2:762288208920:sow-render-jobs
   ```

### IAM Permissions

The Lambda execution role needs:

- `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the render jobs queue
- `s3:GetObject`, `s3:PutObject`, `s3:HeadObject`, `s3:DeleteObject` on the R2 bucket (if using IAM-compatible endpoint)
- `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` for pulling the container image

## Error Handling

- **SQS retries**: Failed messages are retried up to 3 times (via maxReceiveCount), then moved to the DLQ
- **Job failure**: The pipeline marks the DB job as `failed` with an error message before raising
- **Orphan recovery**: Jobs stuck in `running` status for over 30 minutes are recovered to `failed` by `recover_orphaned_jobs()`
- **Cancellation**: The pipeline checks job status between phases; cancelled jobs are skipped without marking as failed
