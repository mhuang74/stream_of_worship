# Stream of Worship Webapp — Deployment Plan

## 1. Architecture Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   GitHub     │────▶│    Vercel    │────▶│  Neon DB     │
│   Actions    │     │  (Next.js)   │     │  (Postgres)  │
│   CI/CD      │     │  us-east-1  │     │  us-east-1   │
└──────┬───────┘     └──────┬───────┘     └──────────────┘
       │                    │
       │                    │ SQS queue
       │                    ▼
       │             ┌──────────────┐     ┌──────────────┐
       │             │  AWS EC2     │────▶│ Cloudflare   │
       │             │  us-west-2   │     │ R2           │
       │             │  (Spot)      │     │ (Storage)    │
       │             └──────────────┘     └──────────────┘
       │                    │
       │                    ▼
       │             ┌──────────────┐
       │             │  Neon DB     │
       │             │  (status)    │
       │             └──────────────┘
       │
       └──── Builds & pushes Docker image to ECR ──▶ EC2 pulls on deploy
```

### Component Responsibilities

| Component | Role | Host |
|-----------|------|------|
| **Next.js App** | Frontend, API routes, auth, songset CRUD, render job creation | Vercel (us-east-1) |
| **Render Worker** | FFmpeg video encoding, audio mixing, R2 upload, fastembed semantic search | AWS EC2 Spot (us-west-2) |
| **SQS Queue** | Decouples render job creation from execution | AWS SQS (us-west-2) |
| **Neon DB** | All application data + job status | Neon (us-east-1) |
| **Cloudflare R2** | Audio/video file storage | Cloudflare (global) |
| **GitHub Actions** | CI: test on PR, deploy on merge to main | GitHub |

---

## 2. Vendor & Service Selection

| Need | Vendor | Tier | Est. Monthly Cost |
|------|--------|------|-------------------|
| Next.js hosting | **Vercel** | Hobby (free) | $0 |
| PostgreSQL | **Neon** | Free (0.5GB, 100 compute-hrs) | $0 |
| Object storage | **Cloudflare R2** | Free tier (10GB storage, 1M Class A ops) | $0–$5 |
| Render compute | **AWS EC2** | t3a.micro Spot (us-west-2) | ~$3–4 |
| Job queue | **AWS SQS** | Free tier (1M requests) | $0 |
| Container registry | **AWS ECR** | Free tier (500MB) | $0 |
| CI/CD | **GitHub Actions** | Free (2,000 min/mo) | $0 |
| DNS + SSL | **Cloudflare** | Free plan | $0 |
| Domain | Custom domain via Cloudflare DNS | — | ~$10/yr |
| **Total** | | | **~$3–9/mo** |

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
- `executeRenderPipeline()` — moves to EC2 worker
- `fastembed` / semantic search — moves to EC2 worker
- `node-canvas` / `ffmpeg-static` — no longer needed on Vercel

**Configuration:**
- Region: `iad1` (us-east-1) — already set in `vercel.json`
- `serverExternalPackages`: Remove `fastembed` and `ffmpeg-static` after migration
- `maxDuration`: Render job API routes can be reduced from 800s since they no longer execute the pipeline
- Framework: Next.js 16 (App Router)

**Vercel project setup:**
1. Connect GitHub repo → Vercel project
2. Root directory: `webapp/`
3. Build command: `pnpm build`
4. Install command: `pnpm install --frozen-lockfile`
5. Environment variables (see Section 5)

### 3.2 AWS EC2 — Render Worker

**What runs here:**
- Python render worker process (Docker container)
- FFmpeg video encoding (libx264)
- Pillow-based frame rendering
- Audio mixing via FFmpeg subprocess
- R2 upload of rendered artifacts
- fastembed semantic search API endpoint
- SQS polling loop

**Instance sizing:**
- **Type:** t3a.micro (2 vCPU, 1GB RAM) — Spot pricing
- **Region:** us-west-2
- **AMI:** Amazon Linux 2023 (for Docker + ECS support)
- **Storage:** 20GB gp3 (temp files for rendering)

**Spot instance considerations:**
- EC2 Spot provides a 2-minute interruption warning via the metadata endpoint
- Worker should check `http://169.254.169.254/latest/meta-data/spot/instance-action` every 30s
- On interruption: stop polling SQS, let visibility timeout expire (message returns to queue), gracefully shut down
- SQS visibility timeout should be set to ~15 minutes (longer than max render time for a single job on t3a.micro)
- Use a Spot Fleet or ASG with spot capacity to auto-replace interrupted instances

**Docker container:**
```
Dockerfile:
  FROM python:3.11-slim
  RUN apt-get update && apt-get install -y ffmpeg fonts-noto-cjk
  COPY worker/ /app/worker/
  RUN pip install -r /app/worker/requirements.txt
  CMD ["python", "-m", "worker.main"]
```

**Worker responsibilities:**
1. Poll SQS queue for render job messages
2. Read job details from Neon DB
3. Download source audio from R2 (via signed URLs)
4. Execute render pipeline (audio mixing + video encoding)
5. Upload rendered artifacts (MP3, MP4, chapters.json) to R2
6. Update job status in Neon DB
7. Delete SQS message on completion

**fastembed on EC2:**
- The worker also exposes a lightweight HTTP endpoint (e.g., port 8080) for semantic search
- Vercel API route `/api/songs/search/semantic` calls the EC2 endpoint instead of running fastembed locally
- Alternative: pre-compute all embeddings via admin CLI and only use pgvector similarity search at runtime (no fastembed needed at query time). This is simpler and recommended if the song catalog doesn't change frequently.

### 3.3 AWS SQS — Job Queue

**Queue configuration:**
- **Name:** `sow-render-jobs`
- **Region:** us-west-2 (same as EC2)
- **Visibility timeout:** 900s (15 min) — must exceed max render time
- **Message retention:** 4 days (default)
- **DLQ:** `sow-render-jobs-dlq` — after 3 failed deliveries, messages go here for debugging
- **Content:** JSON with `{ "jobId": "...", "songsetId": "...", "userId": 123 }`

**Flow:**
1. Vercel API route creates job in DB → enqueues message to SQS
2. EC2 worker polls SQS → receives message → starts render
3. Worker updates DB with progress (same `updateRenderProgress()` logic)
4. On completion: worker deletes SQS message
5. On failure: message returns to queue after visibility timeout (automatic retry)
6. After 3 failures: message goes to DLQ

**Cross-region note:**
- Vercel runs in us-east-1, SQS is in us-west-2
- SQS API calls from Vercel to us-west-2 add ~70ms latency per call
- This is acceptable since it's a one-time enqueue per render job (not in the hot path)
- Alternative: create SQS in us-east-1 and have the EC2 worker in us-west-2 poll cross-region. Adds ~70ms per poll but keeps Vercel→SQS local. Not worth the complexity — the enqueue is infrequent.

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
- The EC2 worker needs DB access for reading job details and updating progress
- Use the same `DATABASE_URL` connection string (Neon supports connections from any IP)
- Neon free tier does not restrict IP-based access
- If cold starts become an issue, a simple keep-alive ping every 4 minutes can prevent suspension (but consumes compute hours)

### 3.5 Cloudflare R2 — Object Storage

**Current usage:**
- Source audio files: `{hashPrefix}/audio.mp3`
- Source LRC files: `{hashPrefix}/lyrics.lrc`
- Rendered outputs: `renders/{jobId}/output.mp3`, `renders/{jobId}/output.mp4`, `renders/{jobId}/chapters.json`
- S3-compatible API via `@aws-sdk/client-s3`

**No changes needed for deployment.** The EC2 worker uses the same R2 credentials and S3-compatible API.

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
Merge to main → Run tests → Deploy to Vercel → Build & push Docker image to ECR → Deploy to EC2
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
      - uses: amondnet/vercel-action@v25  # or Vercel CLI
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
          # Trigger EC2 to pull new image (SSM Run Command or Lambda)
          aws ssm send-command \
            --instance-ids ${{ secrets.EC2_INSTANCE_ID }} \
            --document-name "AWS-RunShellScript" \
            --parameters commands=["docker pull ... && docker-compose up -d"]
```

### 4.2 Vercel Auto-Deploy

Vercel's GitHub integration provides automatic deployments:
- Every push to `main` → production deployment
- Every PR → preview deployment (optional, can be disabled)
- Already configured in `vercel.json` (`git.deploymentEnabled`)

**Recommendation:** Disable Vercel's auto-deploy for `main` and use GitHub Actions instead, so you have a single CI/CD pipeline that handles both Vercel and EC2 deployments atomically.

---

## 5. Environment Variables

### Vercel (Production)

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Neon | `postgresql://...@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require` |
| `R2_ACCOUNT_ID` | Cloudflare | Account ID for R2 |
| `R2_ACCESS_KEY_ID` | Cloudflare | R2 API token |
| `R2_SECRET_ACCESS_KEY` | Cloudflare | R2 API secret |
| `R2_BUCKET_NAME` | Cloudflare | `stream-of-worship-prod` |
| `NEXT_PUBLIC_R2_PUBLIC_DOMAIN` | Cloudflare | Public R2 domain or custom domain |
| `BETTER_AUTH_SECRET` | Generated | `openssl rand -base64 32` |
| `BETTER_AUTH_URL` | Custom domain | `https://your-domain.com` |
| `NEXT_PUBLIC_BASE_URL` | Custom domain | `https://your-domain.com` |
| `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` | Google Cast | Default Media Receiver (v3). Leave unset to use Google's Default Media Receiver constant; one ID per environment is optional and only needed for a future custom receiver. Whitelist Cast test devices by serial in the Cast SDK Developer Console. Production launch = Cast approval (2–4 weeks). See the Live-Service Go/No-Go Checklist in `README.md` |
| `AWS_REGION` | AWS | `us-west-2` |
| `SQS_QUEUE_URL` | AWS | `https://sqs.us-west-2.amazonaws.com/.../sow-render-jobs` |
| `AWS_ACCESS_KEY_ID` | AWS | IAM user for SQS |
| `AWS_SECRET_ACCESS_KEY` | AWS | IAM user for SQS |

### EC2 Worker (Docker env vars)

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Neon | Same as Vercel |
| `R2_ACCOUNT_ID` | Cloudflare | Same as Vercel |
| `R2_ACCESS_KEY_ID` | Cloudflare | Same as Vercel |
| `R2_SECRET_ACCESS_KEY` | Cloudflare | Same as Vercel |
| `R2_BUCKET_NAME` | Cloudflare | Same as Vercel |
| `SQS_QUEUE_URL` | AWS | Same as Vercel |
| `AWS_REGION` | AWS | `us-west-2` |
| `AWS_ACCESS_KEY_ID` | AWS | IAM role (preferred) or IAM user |
| `AWS_SECRET_ACCESS_KEY` | AWS | IAM role (preferred) or IAM user |

---

## 6. Gaps & Required Work

### 6.1 New Code to Write

| Item | Description | Effort |
|------|-------------|--------|
| **Python render worker** | Standalone Python package in `services/render-worker/`. SQS polling loop, render pipeline (Pillow + FFmpeg subprocess), R2 upload, DB status updates. Must achieve feature parity with Node.js pipeline: title cards, chapters, font auto-scaling, crossfade, loudness normalization. | **Large** |
| **SQS integration in Next.js** | Modify `POST /api/render-jobs` to enqueue SQS message instead of calling `executeRenderPipeline()` via `after()`. Add `@aws-sdk/client-sqs` dependency. | **Small** |
| **Remove render pipeline from Next.js** | Remove `serverExternalPackages: ["fastembed", "ffmpeg-static"]` from `next.config.ts`. Remove `canvas`, `ffmpeg-static`, `fluent-ffmpeg`, `fastembed` from `package.json` dependencies. Reduce `maxDuration` on render API routes from 800s. | **Small** |
| **Semantic search proxy** | Either: (a) proxy `/api/songs/search/semantic` to EC2 fastembed endpoint, or (b) remove fastembed entirely and rely on pre-computed pgvector similarity search. Option (b) is recommended and simpler. | **Small–Medium** |
| **GitHub Actions workflows** | `.github/workflows/ci.yml` and `.github/workflows/deploy.yml` | **Small** |
| **Dockerfile for worker** | `services/render-worker/Dockerfile` with Python 3.11, FFmpeg, CJK fonts, Pillow | **Small** |
| **EC2 user data script** | Bootstrap script to install Docker, pull image from ECR, start worker container | **Small** |
| **DB migration runner** | How to run `drizzle-kit migrate` against Neon in CI/CD | **Small** |

### 6.2 Infrastructure to Provision (AWS)

| Resource | Details |
|----------|---------|
| **SQS Queue** | `sow-render-jobs` + DLQ `sow-render-jobs-dlq` |
| **ECR Repository** | `sow-render-worker` |
| **IAM User (Vercel)** | SQS send-only permissions (`sqs:SendMessage`, `sqs:GetQueueUrl`) |
| **IAM Role (EC2)** | SQS receive/delete + ECR pull + CloudWatch Logs. Attach via instance profile. |
| **EC2 Spot Instance** | t3a.micro, us-west-2, Amazon Linux 2023 AMI |
| **Security Group** | Allow outbound 443 (HTTPS) only. No inbound rules needed (worker polls SQS, no incoming connections unless fastembed endpoint is exposed). |
| **CloudWatch Log Group** | `/sow/render-worker` for worker logs |

### 6.3 Infrastructure to Provision (Cloudflare)

| Resource | Details |
|----------|---------|
| **Custom domain DNS** | CNAME record pointing to `cname.vercel-dns.com` |
| **R2 bucket (prod)** | `stream-of-worship-prod` (if not already created) |

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
| Add AWS SDK to worker | `services/render-worker/requirements.txt` | `boto3` for SQS + S3 |
| DB migration in CI | GitHub Actions | Add step to run `npx drizzle-kit migrate` against Neon |

---

## 7. Security Considerations

| Area | Recommendation |
|------|----------------|
| **SQS access** | Vercel uses a dedicated IAM user with send-only permissions. EC2 uses an IAM instance role with receive/delete permissions. Principle of least privilege. |
| **R2 credentials** | Same credentials shared between Vercel and EC2 worker. Consider using IAM role on EC2 for R2 access instead of static credentials. |
| **Neon DB** | Neon requires SSL (`sslmode=require`). No IP allowlisting needed on free tier. |
| **EC2 security group** | No inbound rules. Worker polls SQS (outbound). If fastembed endpoint is exposed, restrict inbound to Vercel IPs only. |
| **Secrets management** | Vercel: encrypted env vars in project settings. EC2: SSM Parameter Store or Secrets Manager for Docker env vars. GitHub: repository secrets. |
| **Better Auth** | `useSecureCookies: true` in production. Ensure `BETTER_AUTH_URL` uses HTTPS. |

---

## 8. Monitoring & Observability

| Signal | Tool | Details |
|--------|------|---------|
| **Vercel logs** | Vercel Dashboard | API route errors, function duration |
| **EC2 worker logs** | CloudWatch Logs | Worker stdout/stderr via Docker logging driver |
| **SQS DLQ** | AWS Console | Failed jobs after 3 retries — needs manual inspection |
| **Render job status** | Neon DB | Query `render_jobs` table for failed/running jobs |
| **Spot interruption** | CloudWatch Events | Rule for Spot Instance Interruption Warning |
| **Uptime** | Vercel Analytics | Built-in Web Vitals + uptime monitoring |

---

## 9. Cost Estimate

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| Vercel Hobby | $0 | Free for personal projects |
| Neon Free | $0 | 0.5GB, 100 compute-hrs |
| Cloudflare R2 | $0–5 | Free tier covers 10GB + 1M Class A ops |
| EC2 t3a.micro Spot | ~$3–4 | ~$0.0047/hr spot price in us-west-2 |
| SQS | $0 | Free tier: 1M requests |
| ECR | $0 | Free tier: 500MB storage |
| GitHub Actions | $0 | Free tier: 2,000 min/mo |
| Cloudflare DNS | $0 | Free plan |
| Domain | ~$0.83/mo | ~$10/yr |
| **Total** | **~$4–10/mo** | Well within $50/mo budget |

---

## 10. Deployment Sequence

### Phase 1: Foundation (no code changes)
1. Create AWS resources: SQS queue + DLQ, ECR repo, IAM user/role
2. Set up Vercel project: connect GitHub, configure env vars, add custom domain
3. Configure Cloudflare DNS: CNAME to Vercel
4. Verify: Vercel deploys successfully, app loads on custom domain

### Phase 2: CI/CD
5. Create `.github/workflows/ci.yml` — test + lint on PR
6. Create `.github/workflows/deploy.yml` — deploy to Vercel on merge
7. Verify: PR triggers tests, merge triggers deployment

### Phase 3: Python Render Worker
8. Create `services/render-worker/` with SQS polling, render pipeline, R2 upload
9. Write Dockerfile and push to ECR
10. Launch EC2 spot instance, deploy worker container
11. Verify: send test message to SQS, worker processes it, output appears in R2

### Phase 4: Integration
12. Modify Next.js `POST /api/render-jobs` to enqueue SQS message
13. Remove `executeRenderPipeline()` from Next.js `after()` call
14. Remove heavy dependencies from `webapp/package.json`
15. Update `next.config.ts` and `vercel.json`
16. Handle semantic search: either proxy to EC2 or switch to pgvector-only
17. Verify: end-to-end render job from webapp UI → SQS → EC2 worker → R2 → download

### Phase 5: Hardening
18. Add Spot interruption handling to worker
19. Set up CloudWatch Logs for worker
20. Add DB migration step to CI pipeline
21. Add monitoring/alerting for DLQ depth
22. Load test with real render jobs

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **t3a.micro too slow for 1080p renders** | 1080p video encoding may take 30+ min on 1GB RAM | Default to 720p. Offer 1080p as "slow" option. Upgrade to t3a.small if needed (~$6/mo spot). |
| **Spot instance interruption kills render** | In-progress render lost | SQS visibility timeout returns message to queue. Worker checkpoints progress to DB. 2-min warning allows graceful shutdown. |
| **Neon cold start latency** | 1-2s delay on first request after idle | Acceptable for low-traffic app. Keep-alive ping if problematic. |
| **Cross-region latency (us-east-1 ↔ us-west-2)** | ~70ms per SQS call from Vercel | Only affects job creation (infrequent). Acceptable. |
| **node-canvas removal breaks something** | Unknown dependency on canvas | Thoroughly audit all imports before removing. Canvas is only used in `frame-renderer.ts` which moves to EC2. |
| **Python worker feature parity** | Missing features vs Node.js pipeline | Write comprehensive tests comparing outputs. Port features incrementally. |
| **R2 egress costs** | Large video downloads could exceed free tier | R2 has zero egress fees. No risk here. |
