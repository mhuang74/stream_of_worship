# Stream of Worship Webapp — Deployment Plan v2

> **Changes from v1:** Replaced always-on EC2 Spot instance with AWS Lambda for
> render compute (pay-per-invocation, higher CPU, no idle cost). Migrated R2
> bucket from APAC to WNAM to co-locate with compute. All backend resources
> consolidated in us-west-2; Vercel remains us-east-1 (Hobby plan constraint).

## 1. Architecture Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   GitHub     │────▶│   Vercel     │────▶│  Neon DB     │
│   Actions    │     │  (Next.js)   │     │  (Postgres)  │
│   CI/CD      │     │  us-east-1   │     │  us-west-2   │
└──────┬───────┘     └──────┬───────┘     └──────────────┘
       │                    │
       │                    │ SQS enqueue (~70ms cross-region, infrequent)
       │                    ▼
       │             ┌──────────────┐     ┌──────────────┐
       │             │  AWS SQS     │────▶│ AWS Lambda   │
       │             │  us-west-2   │     │ us-west-2    │
       │             │  (trigger)   │     │ 4-10GB RAM   │
       │             └──────────────┘     │ (FFmpeg)     │
       │                                  └──────┬───────┘
       │                                         │
       │                              ┌──────────┼──────────┐
       │                              ▼          ▼          │
       │                     ┌──────────┐ ┌──────────┐    │
       │                     │ Neon DB  │ │Cloudflare│    │
       │                     │ (status) │ │R2 WNAM  │    │
       │                     └──────────┘ └──────────┘    │
       │                                                  │
       └──── Builds & pushes Docker image to ECR ────────┘
            (Lambda container image deployment)
```

### Component Responsibilities

| Component | Role | Host |
|-----------|------|------|
| **Next.js App** | Frontend, API routes, auth, songset CRUD, render job creation | Vercel (us-east-1) |
| **Render Worker** | FFmpeg video encoding, audio mixing, R2 upload | AWS Lambda (us-west-2) |
| **SQS Queue** | Decouples render job creation from execution; auto-triggers Lambda | AWS SQS (us-west-2) |
| **Neon DB** | All application data + job status | Neon (us-west-2) |
| **Cloudflare R2** | Audio/video file storage | Cloudflare R2 (WNAM) |
| **GitHub Actions** | CI: test on PR, deploy on merge to main | GitHub |

---

## 2. Vendor & Service Selection

| Need | Vendor | Tier | Est. Monthly Cost |
|------|--------|------|-------------------|
| Next.js hosting | **Vercel** | Hobby (free) | $0 |
| PostgreSQL | **Neon** | Free (0.5GB, 100 compute-hrs) | $0 |
| Object storage | **Cloudflare R2** | Free tier (10GB storage, 1M Class A ops) | $0–$5 |
| Render compute | **AWS Lambda** | 4-10GB RAM, container image | $0* |
| Job queue | **AWS SQS** | Free tier (1M requests) | $0 |
| Container registry | **AWS ECR** | Free tier (500MB) | $0 |
| CI/CD | **GitHub Actions** | Free (2,000 min/mo) | $0 |
| DNS + SSL | **Cloudflare** | Free plan | $0 |
| Domain | Custom domain via Cloudflare DNS | — | ~$10/yr |
| **Total** | | | **~$0–6/mo** |

\*Lambda free tier: 1M requests + 400K GB-seconds/month. A 4GB × 5min render
consumes ~1,200 GB-seconds, allowing ~333 renders/month within free tier.
A 10GB × 5min render consumes ~3,000 GB-seconds, allowing ~133 renders/month
within free tier.

---

## 3. Detailed Component Design

### 3.1 Vercel — Next.js App

**What runs here:**
- All Next.js App Router pages and API routes
- Better Auth (email/password authentication)
- Songset CRUD, song browsing, share links
- Render job creation (POST `/api/render-jobs`) — writes job to DB, enqueues SQS message
- Render job status polling (GET `/api/render-jobs/[id]`)
- Signed URL generation for R2 assets
- Service worker / offline caching (client-side)

**What does NOT run here:**
- `executeRenderPipeline()` — moves to Lambda worker
- `fastembed` / semantic search — pre-computed via admin CLI, pgvector at runtime
- `node-canvas` / `ffmpeg-static` — no longer needed on Vercel

**Configuration:**
- Region: `iad1` (us-east-1) — Hobby plan constraint; Pro ($20/mo) required for `pdx1`
- `serverExternalPackages`: Remove `fastembed` and `ffmpeg-static` after migration
- `maxDuration`: Render job API routes can be reduced from 800s since they no longer execute the pipeline
- Framework: Next.js 16 (App Router)

**Cross-region note:**
- Vercel (us-east-1) → SQS (us-west-2) adds ~70ms per enqueue call
- This is a one-time call per render job (not in the hot path) — acceptable
- All other backend resources (Lambda, Neon, R2) are co-located in us-west-2

**Vercel project setup:**
1. Connect GitHub repo → Vercel project
2. Root directory: `webapp/`
3. Build command: `pnpm build`
4. Install command: `pnpm install --frozen-lockfile`
5. Environment variables (see Section 5)

### 3.2 AWS Lambda — Render Worker

**What runs here:**
- Python render worker (Lambda handler, container image)
- FFmpeg video encoding (libx264)
- Pillow-based frame rendering
- Audio mixing via FFmpeg subprocess
- R2 upload of rendered artifacts
- DB status updates

**Function configuration:**

| Parameter | 720p (default) | 1080p (fallback) |
|-----------|---------------|------------------|
| Memory | 4 GB | 10 GB |
| vCPU (proportional) | ~2 | ~6 |
| Ephemeral storage | 5 GB | 5 GB |
| Timeout | 900s (15 min) | 900s (15 min) |
| Est. render time | 3-5 min | 5-10 min |

**Why Lambda over EC2 Spot:**

| Factor | EC2 Spot t3a.micro | AWS Lambda 4-10GB |
|--------|--------------------|--------------------|
| Idle cost | $3-4/mo always-on | $0 when idle |
| CPU | 2 vCPU (burstable) | 2-6 vCPU (dedicated) |
| RAM | 1 GB | 4-10 GB |
| 1080p render time | 30+ min | 5-10 min |
| Spot interruption | Must handle 2-min warning | No interruption |
| Scaling | Manual / ASG | Automatic (0 → N) |
| Deployment | SSM Run Command | `lambda update-function-code` |
| SQS integration | Custom polling loop | Native event source mapping |

**Lambda container image:**
```
Dockerfile:
  FROM public.ecr.aws/lambda/python:3.11
  RUN dnf install -y ffmpeg fonts-noto-cjk
  COPY worker/ /app/worker/
  RUN pip install -r /app/worker/requirements.txt
  CMD ["worker.lambda_handler.handler"]
```

**Lambda handler structure:**
```python
# worker/lambda_handler.py
def handler(event, context):
    for record in event["Records"]:
        body = json.loads(record["body"])
        job_id = body["jobId"]
        try:
            update_job_status(job_id, "processing")
            download_source_audio(job_id)
            execute_render_pipeline(job_id)
            upload_artifacts(job_id)
            update_job_status(job_id, "completed")
        except Exception as e:
            update_job_status(job_id, "failed", str(e))
            raise  # SQS will retry; after 3 failures → DLQ
```

**SQS event source mapping:**
- Lambda automatically receives SQS messages — no polling loop needed
- Batch size: 1 (one render job per invocation; renders are CPU-heavy)
- Maximum batching window: 0s (invoke immediately on message arrival)
- Lambda reads and deletes SQS messages automatically on successful invocation

**Lambda cold start:**
- Container image cold start: ~1-3 seconds (first invocation after deploy or idle period)
- Subsequent invocations: ~100ms (warm container)
- Acceptable for async render jobs — user is already polling for status
- Provisioned concurrency is available but not needed at this traffic level

**Fallback path — AWS Fargate:**
- If 1080p renders consistently exceed the 15-minute Lambda timeout, migrate to
  AWS Fargate (serverless containers, 60-min timeout, up to 16 vCPU / 120GB RAM)
- Same ECR container image, different compute target
- Fargate cost: ~$0.01/min for 1 vCPU / 4GB — still pay-per-use, no always-on cost
- Document this as a known escalation path; do not implement unless needed

**Worker responsibilities:**
1. Receive SQS message via Lambda event source mapping
2. Read job details from Neon DB
3. Download source audio from R2 (S3-compatible API, same region)
4. Execute render pipeline (audio mixing + video encoding)
5. Upload rendered artifacts (MP3, MP4, chapters.json) to R2
6. Update job status in Neon DB
7. SQS message auto-deleted on successful invocation

**Semantic search:**
- Pre-compute all embeddings via admin CLI and store in pgvector
- Runtime queries use pgvector similarity search only — no fastembed at query time
- This eliminates the need for a persistent fastembed endpoint on the worker
- Simpler architecture, no Lambda Function URL needed for search

### 3.3 AWS SQS — Job Queue

**Queue configuration:**
- **Name:** `sow-render-jobs`
- **Region:** us-west-2 (same as Lambda and Neon)
- **Visibility timeout:** 900s (15 min) — must exceed max Lambda execution time
- **Message retention:** 4 days (default)
- **DLQ:** `sow-render-jobs-dlq` — after 3 failed deliveries, messages go here for debugging
- **Content:** JSON with `{ "jobId": "...", "songsetId": "...", "userId": 123 }`

**Flow:**
1. Vercel API route creates job in DB → enqueues message to SQS
2. SQS event source mapping auto-triggers Lambda with the message
3. Lambda reads job details, executes render pipeline, uploads to R2
4. Lambda updates DB with progress and final status
5. On success: SQS message auto-deleted by Lambda
6. On failure: Lambda raises exception → SQS retries after visibility timeout
7. After 3 failures: message goes to DLQ

**Cross-region note:**
- Vercel (us-east-1) → SQS (us-west-2) adds ~70ms per enqueue call
- This is a one-time call per render job — acceptable
- All other interactions (SQS→Lambda, Lambda→Neon, Lambda→R2) are us-west-2 local

### 3.4 Neon — PostgreSQL

**Current usage:**
- Drizzle ORM with `neon-http` serverless driver
- pgvector extension for `song_embedding` table (1024-dim vectors)
- Better Auth tables (users, accounts, sessions, verifications)
- Application tables (songs, recordings, songsets, render_jobs, etc.)

**Free tier limits:**
- 0.5 GB storage
- 100 compute hours/month
- Auto-suspend after 5 minutes of inactivity
- Cold start: ~1-2 seconds on first request after suspension

**Considerations:**
- Lambda needs DB access for reading job details and updating progress
- Use the same `DATABASE_URL` connection string (Neon supports connections from any IP)
- Neon free tier does not restrict IP-based access
- Lambda and Neon are both in us-west-2 — minimal network latency
- If cold starts become an issue, a simple keep-alive ping every 4 minutes can prevent suspension (but consumes compute hours)

### 3.5 Cloudflare R2 — Object Storage

**Current usage:**
- Source audio files: `{hashPrefix}/audio.mp3`
- Source LRC files: `{hashPrefix}/lyrics.lrc`
- Rendered outputs: `renders/{jobId}/output.mp3`, `renders/{jobId}/output.mp4`, `renders/{jobId}/chapters.json`
- S3-compatible API via `@aws-sdk/client-s3`

**v2 change: Bucket migrated from APAC to WNAM (US West North America)**

**Why WNAM:**
- Lambda render worker (us-west-2) reads source audio and writes rendered output to R2
- Co-locating R2 with compute eliminates ~100-150ms per-file cross-region latency
- Rendering is the most I/O-intensive path — multiple large file reads/writes per job
- R2 egress is free — no cost difference between regions
- R2 CDN caching handles Asia users after first download (~20ms subsequent access)
- NA users (primary base) get best first-access experience from WNAM

**Migration steps:**
1. Create new R2 bucket `stream-of-worship-prod` with WNAM location hint
2. Sync data: `aws s3 sync s3://stream-of-worship-prod-apac s3://stream-of-worship-prod --endpoint-url https://<account-id>.r2.cloudflarestorage.com`
3. Update `R2_BUCKET_NAME` env var in Vercel and Lambda
4. Verify: upload test file, download from both buckets, confirm new bucket works
5. Delete APAC bucket after verification

**Asia user experience:**
- First download from WNAM: ~80-120ms latency
- Subsequent downloads: ~20ms (Cloudflare edge CDN cache)
- Acceptable trade-off given NA is the primary user base

### 3.6 Cloudflare DNS — Custom Domain

**Setup:**
1. Add custom domain in Vercel project settings
2. Add CNAME record in Cloudflare DNS pointing to `cname.vercel-dns.com`
3. Vercel provisions SSL certificate automatically
4. Update environment variables:
   - `BETTER_AUTH_URL=https://your-domain.com`
   - `NEXT_PUBLIC_BASE_URL=https://your-domain.com`
   - `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` (leave unset to use Google Default Media Receiver; one ID per environment, set only if a custom receiver is reintroduced in future — see `delivery/webapp/README.md` → Google Cast SDK Setup)

### 3.7 Google Cast (Default Media Receiver)

v3 of the worship controller casts an MP4 to a Google TV / Chromecast via the
Google Cast **Web Sender SDK** using Google's built-in **Default Media
Receiver** (`chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID` when
`NEXT_PUBLIC_CAST_RECEIVER_APP_ID` is unset). This is the only supported v3
Cast mode — no custom on-receiver registration is required.

- **Receiver registration (default only).** Whitelist the Cast test devices
  by serial number in the Cast SDK Developer Console
  (<https://cast.google.com/publish> → **Device registration**) for dev/staging.
  Cast review is the production launch gate (Submit for Approval, 2–4 weeks).
  One `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` is allowed per environment, or omit it
  to use the Default Media Receiver constant.
- **Lyrics are baked into the MP4** — no custom Cast receiver UI is needed.
- **4-hour signed URL policy.** The logged-in phone mints the MP4 at a
  14400-second (4-hour) presigned R2 URL via
  `POST /api/signed-url?cast=true` (songset ownership path, session-required)
  or `GET /api/share/[token]` (public share path). The phone hands the URL to
  the TV receiver; the receiver only hits R2, never the webapp. Services
  longer than ~3h40m require a deliberate stop/re-cast before URL expiry.
- **faststart requirement.** The render worker (`delivery/render-worker/`)
  emits H.264 + AAC MP4s with `-movflags +faststart` (moov atom placed at the
  front for fast startup / range-seek on TV hardware). The
  `test_mp4_cast_compatibility.py` ffprobe pipeline test asserts H.264 / AAC /
  moov-before-mdat, and the upload `content_type` is enforced as
  `video/mp4`.
- **Runbook (pre-service):** phone + TV on the same Wi-Fi/VLAN (no captive
  portal / guest isolation); receiver fetches the MP4 directly from R2; R2 must
  respond `Content-Type: video/mp4` + accept range requests. Open the MP4 URL
  in a laptop browser on the same network and verify range-seek (forward/back
  10s, reload) before the service. iPhone web does not support Chromecast —
  use AirPlay to Apple TV instead. Presentation API = dev-only fallback.

See the **Live-Service Go/No-Go Checklist** in
`delivery/webapp/README.md` (10 items) before any first live use on the same
TV + network class used in service.

---

## 4. CI/CD Pipeline

### 4.1 GitHub Actions Workflow

```
PR opened → Run tests (pnpm test) + lint (pnpm lint)
Merge to main → Run tests → Deploy to Vercel → Build & push Docker image to ECR → Update Lambda function
```

**Workflow files needed:**
- `.github/workflows/ci.yml` — Test + lint on PR
- `.github/workflows/deploy.yml` — Deploy on merge to main

**CI workflow (on PR):**
```yaml
# .github/workflows/ci.yml
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm, cache-dependency-path: webapp/pnpm-lock.yaml }
      - run: pnpm install --frozen-lockfile
        working-directory: webapp
      - run: pnpm lint
        working-directory: webapp
      - run: pnpm test
        working-directory: webapp
```

**Deploy workflow (on merge to main):**
```yaml
# .github/workflows/deploy.yml
on:
  push:
    branches: [main]
    paths: [webapp/**, services/render-worker/**]
jobs:
  deploy-vercel:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: amondnet/vercel-action@v25
        with:
          vercel-token: ${{ secrets.VERCEL_TOKEN }}
          vercel-org-id: ${{ secrets.VERCEL_ORG_ID }}
          vercel-project-id: ${{ secrets.VERCEL_PROJECT_ID }}
          working-directory: webapp

  deploy-worker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-west-2
      - uses: aws-actions/amazon-ecr-login@v2
      - run: |
          docker build -t sow-render-worker services/render-worker/
          docker tag sow-render-worker:latest $ECR_REGISTRY/sow-render-worker:latest
          docker push $ECR_REGISTRY/sow-render-worker:latest
      - run: |
          aws lambda update-function-code \
            --function-name sow-render-worker \
            --image-uri $ECR_REGISTRY/sow-render-worker:latest \
            --publish
```

### 4.2 Vercel Auto-Deploy

Vercel's GitHub integration provides automatic deployments:
- Every push to `main` → production deployment
- Every PR → preview deployment (optional, can be disabled)
- Already configured in `vercel.json` (`git.deploymentEnabled`)

**Recommendation:** Disable Vercel's auto-deploy for `main` and use GitHub Actions instead, so you have a single CI/CD pipeline that handles both Vercel and Lambda deployments atomically.

---

## 5. Environment Variables

### Vercel (Production)

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Neon | `postgresql://...@ep-xxx.us-west-2.aws.neon.tech/neondb?sslmode=require` |
| `R2_ACCOUNT_ID` | Cloudflare | Account ID for R2 |
| `R2_ACCESS_KEY_ID` | Cloudflare | R2 API token |
| `R2_SECRET_ACCESS_KEY` | Cloudflare | R2 API secret |
| `R2_BUCKET_NAME` | Cloudflare | `stream-of-worship-prod` (WNAM) |
| `NEXT_PUBLIC_R2_PUBLIC_DOMAIN` | Cloudflare | Public R2 domain or custom domain |
| `BETTER_AUTH_SECRET` | Generated | `openssl rand -base64 32` |
| `BETTER_AUTH_URL` | Custom domain | `https://your-domain.com` |
| `NEXT_PUBLIC_BASE_URL` | Custom domain | `https://your-domain.com` |
| `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` | Google Cast | Default Media Receiver (v3). Leave unset to use Google's Default Media Receiver constant; one ID per environment is optional and only needed for a future custom receiver. Whitelist Cast test devices by serial in the Cast SDK Developer Console. Production launch = Cast approval (2–4 weeks). See the Live-Service Go/No-Go Checklist in `README.md` |
| `AWS_REGION` | AWS | `us-west-2` |
| `SQS_QUEUE_URL` | AWS | `https://sqs.us-west-2.amazonaws.com/.../sow-render-jobs` |
| `AWS_ACCESS_KEY_ID` | AWS | IAM user for SQS |
| `AWS_SECRET_ACCESS_KEY` | AWS | IAM user for SQS |

### Lambda Function (env vars configured via Lambda console/CLI)

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Neon | Same as Vercel |
| `R2_ACCOUNT_ID` | Cloudflare | Same as Vercel |
| `R2_ACCESS_KEY_ID` | Cloudflare | Same as Vercel |
| `R2_SECRET_ACCESS_KEY` | Cloudflare | Same as Vercel |
| `R2_BUCKET_NAME` | Cloudflare | Same as Vercel |
| `AWS_REGION` | AWS | `us-west-2` |

Note: Lambda uses its IAM execution role for SQS and ECR permissions — no static
AWS credentials needed in env vars. R2 credentials are still required since R2
is outside AWS IAM.

---

## 6. Gaps & Required Work

### 6.1 New Code to Write

| Item | Description | Effort |
|------|-------------|--------|
| **Python render worker** | Standalone Python package in `services/render-worker/`. Lambda handler entry point, render pipeline (Pillow + FFmpeg subprocess), R2 upload, DB status updates. Must achieve feature parity with Node.js pipeline: title cards, chapters, font auto-scaling, crossfade, loudness normalization. | **Large** |
| **SQS integration in Next.js** | Modify `POST /api/render-jobs` to enqueue SQS message instead of calling `executeRenderPipeline()` via `after()`. Add `@aws-sdk/client-sqs` dependency. | **Small** |
| **Remove render pipeline from Next.js** | Remove `serverExternalPackages: ["fastembed", "ffmpeg-static"]` from `next.config.ts`. Remove `canvas`, `ffmpeg-static`, `fluent-ffmpeg`, `fastembed` from `package.json` dependencies. Reduce `maxDuration` on render API routes from 800s. | **Small** |
| **Semantic search migration** | Remove fastembed from runtime. Pre-compute all embeddings via admin CLI and rely on pgvector similarity search at query time. | **Small–Medium** |
| **GitHub Actions workflows** | `.github/workflows/ci.yml` and `.github/workflows/deploy.yml` | **Small** |
| **Dockerfile for Lambda** | `services/render-worker/Dockerfile` based on `public.ecr.aws/lambda/python:3.11`, with FFmpeg, CJK fonts, Pillow | **Small** |
| **DB migration runner** | How to run `drizzle-kit migrate` against Neon in CI/CD | **Small** |

### 6.2 Infrastructure to Provision (AWS)

| Resource | Details |
|----------|---------|
| **SQS Queue** | `sow-render-jobs` + DLQ `sow-render-jobs-dlq` (us-west-2) |
| **ECR Repository** | `sow-render-worker` (us-west-2) |
| **Lambda Function** | `sow-render-worker` — container image, 4GB RAM (default), 5GB ephemeral storage, 900s timeout |
| **Lambda IAM Execution Role** | SQS receive/delete, R2 S3-compatible access, Neon DB outbound, ECR pull, CloudWatch Logs |
| **SQS→Lambda Event Source Mapping** | Batch size 1, no batching window, auto-trigger on message arrival |
| **IAM User (Vercel)** | SQS send-only permissions (`sqs:SendMessage`, `sqs:GetQueueUrl`) |
| **CloudWatch Log Group** | `/aws/lambda/sow-render-worker` for worker logs |

### 6.3 Infrastructure to Provision (Cloudflare)

| Resource | Details |
|----------|---------|
| **Custom domain DNS** | CNAME record pointing to `cname.vercel-dns.com` |
| **R2 bucket (prod, WNAM)** | `stream-of-worship-prod` with WNAM location hint |
| **R2 bucket migration** | Sync data from APAC bucket, verify, delete APAC bucket |

### 6.4 Infrastructure to Provision (Vercel)

| Resource | Details |
|----------|---------|
| **Project** | Connect GitHub repo, set root directory to `webapp/` |
| **Environment variables** | All variables from Section 5 |
| **Custom domain** | Add domain in project settings |

### 6.5 Configuration Changes

| Change | File | Details |
|--------|------|---------|
| Remove `serverExternalPackages` | `webapp/next.config.ts` | Remove `fastembed`, `ffmpeg-static` after migration |
| Reduce `maxDuration` | `webapp/vercel.json` | Render API routes no longer need 800s; reduce to 60s (just SQS enqueue) |
| Remove heavy deps | `webapp/package.json` | Remove `canvas`, `ffmpeg-static`, `fluent-ffmpeg`, `fastembed` |
| Add SQS SDK | `webapp/package.json` | Add `@aws-sdk/client-sqs` |
| Add AWS SDK to worker | `services/render-worker/requirements.txt` | `boto3` for S3 (R2), `psycopg2` or `asyncpg` for DB |
| DB migration in CI | GitHub Actions | Add step to run `npx drizzle-kit migrate` against Neon |

---

## 7. Security Considerations

| Area | Recommendation |
|------|----------------|
| **SQS access** | Vercel uses a dedicated IAM user with send-only permissions. Lambda uses its IAM execution role with receive/delete permissions. Principle of least privilege. |
| **R2 credentials** | Vercel uses static R2 credentials (env vars). Lambda uses static R2 credentials (env vars) since R2 is outside AWS IAM. Consider migrating to IAM roles for R2 if Cloudflare adds OIDC support. |
| **Neon DB** | Neon requires SSL (`sslmode=require`). No IP allowlisting needed on free tier. |
| **Lambda execution role** | Least-privilege IAM role: SQS receive/delete, CloudWatch Logs, no admin access. No inbound network rules (Lambda is invoked by SQS, not by external traffic). |
| **Secrets management** | Vercel: encrypted env vars in project settings. Lambda: encrypted env vars via AWS Lambda console (AWS-managed KMS). GitHub: repository secrets. |
| **Better Auth** | `useSecureCookies: true` in production. Ensure `BETTER_AUTH_URL` uses HTTPS. |

---

## 8. Monitoring & Observability

| Signal | Tool | Details |
|--------|------|---------|
| **Vercel logs** | Vercel Dashboard | API route errors, function duration |
| **Lambda logs** | CloudWatch Logs | `/aws/lambda/sow-render-worker` — automatic via Lambda integration |
| **Lambda metrics** | CloudWatch Metrics | Duration, errors, throttles, concurrent executions — automatic |
| **SQS DLQ** | AWS Console | Failed jobs after 3 retries — needs manual inspection |
| **Render job status** | Neon DB | Query `render_jobs` table for failed/running jobs |
| **Lambda timeout alerts** | CloudWatch Alarms | Alert if Lambda approaches or hits 15-min timeout |
| **Uptime** | Vercel Analytics | Built-in Web Vitals + uptime monitoring |

---

## 9. Cost Estimate

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| Vercel Hobby | $0 | Free for personal projects |
| Neon Free | $0 | 0.5GB, 100 compute-hrs |
| Cloudflare R2 | $0–5 | Free tier covers 10GB + 1M Class A ops |
| AWS Lambda | $0* | Free tier: 1M req + 400K GB-sec/mo |
| SQS | $0 | Free tier: 1M requests |
| ECR | $0 | Free tier: 500MB storage |
| GitHub Actions | $0 | Free tier: 2,000 min/mo |
| Cloudflare DNS | $0 | Free plan |
| Domain | ~$0.83/mo | ~$10/yr |
| **Total** | **~$0–6/mo** | Well within $50/mo budget |

\*Lambda cost detail:
- 4GB × 5min render = 1,200 GB-sec per job
- Free tier covers 400,000 GB-sec/mo → ~333 renders/month free
- 10GB × 5min render = 3,000 GB-sec per job → ~133 renders/month free
- Beyond free tier: $0.0000166667/GB-sec → ~$0.02 per 4GB render, ~$0.05 per 10GB render

---

## 10. Deployment Sequence

### Phase 1: Foundation (no code changes)
1. Create AWS resources: SQS queue + DLQ, ECR repo, IAM user (Vercel SQS send-only)
2. Create Lambda function: IAM execution role, container image config (deploy placeholder first)
3. Configure SQS→Lambda event source mapping
4. Set up Vercel project: connect GitHub, configure env vars, add custom domain
5. Configure Cloudflare DNS: CNAME to Vercel
6. Create new R2 bucket with WNAM location hint, sync data from APAC bucket
7. Verify: Vercel deploys successfully, app loads on custom domain, Lambda receives test SQS message

### Phase 2: CI/CD
8. Create `.github/workflows/ci.yml` — test + lint on PR
9. Create `.github/workflows/deploy.yml` — deploy to Vercel + update Lambda on merge
10. Verify: PR triggers tests, merge triggers deployment

### Phase 3: Python Render Worker
11. Create `services/render-worker/` with Lambda handler, render pipeline, R2 upload
12. Write Lambda Dockerfile and push to ECR
13. Deploy Lambda function from ECR image
14. Verify: send test message to SQS, Lambda processes it, output appears in R2

### Phase 4: Integration
15. Modify Next.js `POST /api/render-jobs` to enqueue SQS message
16. Remove `executeRenderPipeline()` from Next.js `after()` call
17. Remove heavy dependencies from `webapp/package.json`
18. Update `next.config.ts` and `vercel.json`
19. Migrate semantic search to pgvector-only (pre-compute embeddings via admin CLI)
20. Verify: end-to-end render job from webapp UI → SQS → Lambda worker → R2 → download

### Phase 5: Hardening
21. Set up CloudWatch Logs and Metrics for Lambda
22. Add Lambda timeout alert (CloudWatch Alarm)
23. Add DB migration step to CI pipeline
24. Add monitoring/alerting for DLQ depth
25. Load test with real render jobs
26. Delete APAC R2 bucket after confirming WNAM bucket is fully operational
27. Document Fargate fallback path if 1080p renders consistently exceed 15-min Lambda timeout

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Lambda 15-min timeout** | 1080p renders may exceed limit | Default to 720p (3-5 min on 4GB). Use 10GB for 1080p (5-10 min). Fallback to AWS Fargate if chronic. |
| **Lambda cold start** | ~1-3s delay on first invocation after idle | Acceptable for async render jobs — user is already polling for status. Provisioned concurrency available if needed. |
| **Lambda container image size** | Images >500MB have slower cold starts | Keep image lean: `python:3.11-slim` base, only FFmpeg + CJK fonts. Target <500MB. |
| **Neon cold start latency** | 1-2s delay on first request after idle | Acceptable for low-traffic app. Keep-alive ping if problematic. |
| **Cross-region latency (Vercel us-east-1 → SQS us-west-2)** | ~70ms per SQS enqueue call | Only affects job creation (infrequent, one-time per job). Acceptable. |
| **node-canvas removal breaks something** | Unknown dependency on canvas | Thoroughly audit all imports before removing. Canvas is only used in `frame-renderer.ts` which moves to Lambda. |
| **Python worker feature parity** | Missing features vs Node.js pipeline | Write comprehensive tests comparing outputs. Port features incrementally. |
| **R2 egress costs** | Large video downloads could exceed free tier | R2 has zero egress fees. No risk here. |
| **Lambda ephemeral storage limit** | 5GB /tmp may be insufficient for large renders | Monitor temp file sizes during load testing. Increase up to 10GB if needed (additional cost). |
