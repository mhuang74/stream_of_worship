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

### 2. Next.js API Route (`delivery/webapp/src/app/api/render-jobs/route.ts`)

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

#### Title card lead-in

When both `videoEnabled` and `includeTitleCard` are on, the mix is built with the card's silence baked in: `generate_songset_audio` prepends
`title_card_lead_in_seconds(first_sung_lyric, card_duration)` — the shortfall against `card_end + 1 s`, zero when the card already clears the first line. The card then covers silence and the first lyric is never hidden.

Because the silence is part of the uploaded MP3 (and therefore the MP4), every artifact shares one timeline; `chapters.json` additionally records it as `titleCardOffsetSeconds` so the webapp can compare chapter times against the Song Arrangement timeline, which starts at 0. Audio-only renders are unchanged.

### 6. Progress Tracking (Pull-Based)

There is **no push callback** from Lambda to the webapp. Instead:

- The Lambda worker updates the shared PostgreSQL database directly at each phase transition
- The browser subscribes to `GET /api/render-jobs/[id]/events` (SSE endpoint in `delivery/webapp/src/app/api/render-jobs/[id]/events/route.ts`)
- The SSE endpoint polls the DB every 1 second and streams updates to the client
- Terminal states (completed/failed/cancelled) close the SSE stream
- Max SSE duration: 30 minutes

### 7. Orphan Recovery

Both the webapp (`recoverOrphanedJobs()` in `delivery/webapp/src/lib/render/job-manager.ts`) and the Lambda worker (`recover_orphaned_jobs()` in `db.py`) independently detect jobs stuck in `running` for over 30 minutes and mark them as `failed`.

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

- Python 3.13 — the shared image is built from `public.ecr.aws/lambda/python:3.13`, whose inherited `com.amazonaws.lambda.feature.managedinstances` label is `Supported` (the 3.11 base sets it to `NotSupported`, which blocks Managed Instances)
- Docker (for containerized builds)
- FFmpeg and CJK fonts (installed in the Docker image)
- AWS account with SQS, Lambda, and private ECR access
- AWS Lambda Managed Instances capacity provider for the Glue worker (standard Lambda's 1800 s asynchronous cap is below the required 3300 s)
- Cloudflare R2 account (credentials required for both build time and runtime)
- Neon PostgreSQL database
- A `.env` file with R2 credentials (used at Docker build time via build args and at runtime)

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SOW_DATABASE_URL` | Neon PostgreSQL connection string |
| `SOW_R2_BUCKET` | Cloudflare R2 bucket name |
| `SOW_R2_ENDPOINT_URL` | R2 S3-compatible endpoint (`https://<account-id>.r2.cloudflarestorage.com`) |
| `SOW_R2_ACCESS_KEY_ID` | R2 access key ID (also used at Docker build time) |
| `SOW_R2_SECRET_ACCESS_KEY` | R2 secret access key (also used at Docker build time) |
| `SOW_AWS_REGION` | AWS region for SQS and Lambda (default: `us-west-2`) |
| `SOW_GLUE_AWS_REGION` | Optional webapp-side region for signing Glue queue sends; must match the `SOW_GLUE_SQS_QUEUE_URL` region and falls back to `SOW_AWS_REGION` when empty |
| `SOW_SQS_QUEUE_URL` | SQS queue URL for render job messages |
| `SOW_GLUE_SQS_QUEUE_URL` | SQS queue URL for Glue (AI transition) job messages — separate from the render queue |
| `SOW_ELEVENLABS_API_KEY` | ElevenLabs key for paid Glue stages. Worker/server only: never `NEXT_PUBLIC_*`, never logged, never set on the render-worker function. Unset disables paid Glue with `GLUE_NOT_CONFIGURED` before reservation/spend |
| `GLUE_CAPACITY_PROVIDER_ARN` | Deploy-time only: Lambda Managed Instances capacity provider ARN for `deploy/aws-up-glue.sh` |

Copy `.env.example` to `.env` and fill in the values for local development.

## Build Args

The Docker image requires R2 credentials at build time to download FFmpeg from R2. These are passed as build args:

| Build Arg | Description |
|-----------|-------------|
| `R2_BUCKET` | Cloudflare R2 bucket name |
| `R2_ENDPOINT_URL` | R2 S3-compatible endpoint |
| `R2_ACCESS_KEY_ID` | R2 access key ID |
| `R2_SECRET_ACCESS_KEY` | R2 secret access key |
| `FFMPEG_VERSION` | FFmpeg version to download (default: `7.0.2`) |

FFmpeg is sourced from R2 (`build-dependencies/ffmpeg/`) with johnvansickle.com as a timed fallback (60s timeout). The shared `scripts/download-ffmpeg.sh` script handles the download logic for both `Dockerfile` and `Dockerfile.dev`.

### Uploading FFmpeg to R2

FFmpeg binaries must be uploaded to R2 before the Docker build can use them as the primary source. Use the `scripts/upload-ffmpeg-to-r2.sh` script from the `delivery/render-worker/` directory:

```bash
# Ensure R2 credentials are set in your environment (or source .env)
cd delivery/render-worker
./scripts/upload-ffmpeg-to-r2.sh 7.0.2
```

The script:
1. Downloads the FFmpeg static build from johnvansickle.com
2. Computes a SHA256 checksum (`--tag` format for portability)
3. Uploads both files to `s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/`
4. Verifies the upload by listing the R2 prefix

Required environment variables: `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`

### Updating FFmpeg Version

When a new johnvansickle release is available:

1. Upload the new version to R2:
   ```bash
   cd delivery/render-worker && ./scripts/upload-ffmpeg-to-r2.sh X.Y.Z
   ```
2. Update the `FFMPEG_VERSION` ARG default in both `Dockerfile` and `Dockerfile.dev`:
   ```dockerfile
   ARG FFMPEG_VERSION=X.Y.Z
   ```
3. Run the full test suite
4. Commit and deploy

## Deploying to Lambda

`.github/workflows/deploy.yml`, job *Deploy Render Worker to Lambda*, runs on every push to `main` that touches `delivery/render-worker/**`. It builds the image, pushes it to the private ECR repo `sow-render-worker` tagged with the commit SHA, then calls `aws lambda update-function-code --publish`. There is no manual step; `deploy/deploy.sh` (the `:latest` path) is for local experiments and skips the R2-FFmpeg gate.

Two properties of the base image and the registry path are load-bearing — both have already broken a deploy:

**Dependencies must have a cp313 wheel the Lambda base image accepts.** `public.ecr.aws/lambda/python:3.13` is Amazon Linux 2023 (glibc 2.34) with no compiler, so a package without a `cp313` manylinux wheel resolves to its sdist and the build dies with `No such file or directory: 'gcc'`. Both Dockerfiles pass `--only-binary=:all:`, which turns that into an immediate resolution error naming the package. This is why `requirements.txt` requires `numpy>=2.1` (numpy 1.26 has no cp313 wheel) and pins `soundfile==0.12.1`.

**The base image must be Python 3.13 or later.** Lambda Managed Instances only accept runtimes that declare `com.amazonaws.lambda.feature.managedinstances="Supported"`; the 3.11 base image sets it to `NotSupported`, so `CreateFunction`/`UpdateFunctionConfiguration` on the Glue capacity provider fails. The label is inherited from the base image, which is why the shared image is built from 3.13.

**The pushed image must be a Docker schema2 manifest.** `UpdateFunctionCode` rejects OCI image indexes and provenance attestations with `The image manifest, config or layer media type for the source image ... is not supported`. Docker 29 with the containerd image store emits an OCI index plus an attestation manifest by default, so the build runs through buildx with `--platform linux/amd64 --provenance=false --sbom=false` and pushes inside the build via `--output type=image,…,push=true,oci-mediatypes=false`. Compare `aws ecr describe-images --query 'imageDetails[*].imageManifestMediaType'` (`application/vnd.docker.distribution.manifest.v2+json`) when a deploy fails this way.

## Local Development

### Run Tests

```bash
cd delivery/render-worker && uv run --extra dev pytest tests/ -v
```

### Run a Single Test File

```bash
cd delivery/render-worker && uv run --extra dev pytest tests/test_pipeline.py -v
```

## Local Testing with Docker

The Docker Compose setup runs the Lambda container locally with the Lambda Runtime Interface Emulator (RIE) on port 9000.

### Build and Start

```bash
docker compose up --build
```

R2 credentials are passed as build args from the `.env` file via docker compose variable substitution.

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

## Local Development with REST Mode

For local development without an SQS queue, you can invoke the render worker directly via its Lambda RIE endpoint. This is useful when you want to test the full render pipeline without setting up AWS SQS.

### Setup

1. **Terminal 1: Start the render worker**
   ```bash
   cd delivery/render-worker && docker compose up --build
   ```

2. **Terminal 2: Start the webapp with REST mode**
   ```bash
   cd delivery/webapp
   SOW_RENDER_WORKER_MODE=rest pnpm dev
   ```

### How It Works

When `SOW_RENDER_WORKER_MODE=rest`, the webapp:
1. Creates the render job in the database (status: "queued")
2. Sends a POST request to the Lambda RIE endpoint (`http://localhost:9000/2015-03-31/functions/function/invocations`)
3. The request payload is wrapped in SQS `Records` format, so the Lambda handler works unchanged
4. The API route **blocks** until the Lambda completes (video renders take 4+ minutes)
5. The browser polls the database for progress via SSE, just like in production

### Concurrency Note

REST mode sends all jobs to a single Docker container with no concurrency limit. For local development with a single user, this is fine. If the container crashes (e.g., OOM), orphan recovery will mark the job as failed after 30 minutes.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOW_RENDER_WORKER_MODE` | `sqs` | Set to `rest` for local dev without SQS |
| `SOW_RENDER_WORKER_REST_URL` | `http://localhost:9000/2015-03-31/functions/function/invocations` | RIE endpoint URL |

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
   docker build \
     --build-arg R2_BUCKET=$SOW_R2_BUCKET \
     --build-arg R2_ENDPOINT_URL=$SOW_R2_ENDPOINT_URL \
     --build-arg R2_ACCESS_KEY_ID=$SOW_R2_ACCESS_KEY_ID \
     --build-arg R2_SECRET_ACCESS_KEY=$SOW_R2_SECRET_ACCESS_KEY \
     -t sow-render-worker .
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

Pushes to `main` that modify `delivery/render-worker/` trigger the deploy workflow in `.github/workflows/deploy.yml`, which:

1. Configures AWS credentials from repository secrets
2. Logs in to private ECR
3. Builds, tags, and pushes the Docker image
4. Updates the Lambda function code with the new image URI

Required GitHub secrets: `SOW_AWS_ACCESS_KEY_ID`, `SOW_AWS_SECRET_ACCESS_KEY`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`

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

### Glue Worker (AI Transitions)

The Glue worker runs the **same ECR image** as the render worker but as a
separate Lambda function and queue, so paid AI-transition generation never
reduces render throughput.

| Setting | Value |
|---|---|
| Queue | `sow-glue-jobs` |
| Dead-letter queue | `sow-glue-jobs-dlq` |
| Visibility timeout | 3500 s (must exceed the function timeout) |
| Redrive | `maxReceiveCount: 3` |
| Function | `sow-glue-worker` on AWS Lambda **Managed Instances** |
| Function timeout | 3300 s — covers the 990 s upload + 1890 s compose reserves (standard Lambda's 1800 s asynchronous cap is not eligible) |
| Event-source mapping | batch size 1, `ReportBatchItemFailures` (Managed Instances reject `MaximumConcurrency`; provider concurrency is capped by max 2 execution environments x `PerExecutionEnvironmentMaxConcurrency=1`) |
| Provider concurrency | 2 = `MaxExecutionEnvironments=2` x `PerExecutionEnvironmentMaxConcurrency=1`. This is the only cap Managed Instances allow: the event-source mapping rejects `ScalingConfig.MaximumConcurrency`, and `ProvisionedPollerConfig.MaximumPollers` sizes the prefetch window rather than in-flight jobs |

Run `deploy/aws-up-glue.sh` (requires `GLUE_CAPACITY_PROVIDER_ARN`; the script
fails fast without it and never prints secrets) to create or update the queue,
DLQ, Managed Instances function, event-source mapping, and a published
version.

Unqualified invocations (the event-source mapping) resolve to the special
`$LATEST.PUBLISHED` version, whose configuration is a snapshot. Changing the
function configuration does **not** change what runs: republish with
`aws lambda publish-version --function-name sow-glue-worker --publish-to LATEST_PUBLISHED`
(a plain `publish-version` only creates a numbered version the mapping never
invokes), wait for `State=Active`, then re-apply
`put-function-scaling-config --qualifier '$LATEST.PUBLISHED'`.

The published timeout is 3300 s because the worker's own deadline gates need
the sum of its provider-stage reserves: an upload may consume its whole 990 s
before compose re-checks its own 1890 s. SQS visibility (3500 s) must stay
above the function timeout, and the CloudWatch operation budget matches it.

`SOW_ELEVENLABS_API_KEY` is set on the **Glue function only**. It is never set
on the render-worker function, never returned to the browser, and never
`NEXT_PUBLIC_*`. If the key is missing, the job fails with
`GLUE_NOT_CONFIGURED` before any paid reservation or spend. Creating the key
and making paid calls requires separate authorization (spec §7.3).

Provider account prerequisites: a paid ElevenLabs workspace with Music
inpainting access, sufficient credits, supported model `music_v2_5`, and
capacity for concurrency 2. Account entitlement/plan failures are
non-retryable in place.

Run `deploy/glue-observability.sh` to create the CloudWatch metric filters and
alarms for DLQ arrivals, ambiguous provider outcomes, stale queue age, missing
heartbeats, and staging-recovery spikes. The same script publishes the spec
§4.2 counters under the `SOW/Glue` namespace: paid attempts
(`GluePaidAttempt`), claimed/failed jobs, redeliveries, cache hits, cached
copyright rejections, persisted raw responses, staging recoveries and staging
faults. Set `GLUE_ALARM_SNS_TOPIC_ARN` to route alarm notifications. Operator
recovery is documented in `docs/glue-operator-runbook.md`, keyed by
`glue_jobs.id`.

### IAM Permissions

The Lambda execution role needs:

- `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the render jobs queue
- `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on `sow-glue-jobs`
- `s3:GetObject`, `s3:PutObject`, `s3:HeadObject`, `s3:DeleteObject` on the R2 bucket (if using IAM-compatible endpoint)
- `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` for pulling the container image
- `logs:CreateLogStream`, `logs:PutLogEvents` on the `sow-glue-worker` log group

The deploying principal (local admin profile or the GitHub deploy role) needs,
in addition to the above:

- `lambda:GetFunctionConfiguration`, `lambda:GetFunction`, `lambda:ListFunctions`
- `lambda:CreateFunction`, `lambda:UpdateFunctionCode`, `lambda:UpdateFunctionConfiguration`
- `lambda:PublishVersion`, `lambda:PutFunctionScalingConfig`
- `lambda:ListEventSourceMappings`, `lambda:CreateEventSourceMapping`, `lambda:UpdateEventSourceMapping`
- `sqs:CreateQueue`, `sqs:GetQueueUrl`, `sqs:GetQueueAttributes`, `sqs:SetQueueAttributes`
  on `sow-glue-jobs` and `sow-glue-jobs-dlq`
- `iam:PassRole` for the Glue worker execution role
- `logs:DescribeLogGroups`, `logs:CreateLogGroup`, `logs:PutMetricFilter`
- `cloudwatch:PutMetricAlarm`
- `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`,
  `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`
  for image pushes

## Error Handling

- **SQS retries**: Failed messages are retried up to 3 times (via maxReceiveCount), then moved to the DLQ
- **Job failure**: The pipeline marks the DB job as `failed` with an error message before raising
- **Orphan recovery**: Jobs stuck in `running` status for over 30 minutes are recovered to `failed` by `recover_orphaned_jobs()`
- **Cancellation**: The pipeline checks job status between phases; cancelled jobs are skipped without marking as failed
