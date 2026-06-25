# Stream of Worship Web App

Web application for rendering worship music transitions with synchronized lyrics videos.

## Prerequisites

- Node.js 18+
- pnpm
- PostgreSQL database (Neon recommended)
- PostgreSQL `pgvector` extension enabled
- Cloudflare R2 account

## Environment Setup

Copy `.env.example` to `.env.local` and configure:

- `SOW_DATABASE_URL` — PostgreSQL connection string
- `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`, `SOW_R2_BUCKET` — Cloudflare R2 credentials
- `SOW_AWS_REGION`, `SOW_SQS_QUEUE_URL`, `SOW_AWS_ACCESS_KEY_ID`, `SOW_AWS_SECRET_ACCESS_KEY` — AWS SQS credentials for render job queue
- `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL` — Better Auth configuration
- `NEXT_PUBLIC_BASE_URL` — Base URL of the app (for share links)
- `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` — (optional) Google Cast Web Sender SDK receiver app ID. Omit to use Google's Default Media Receiver, which is the only supported v3 Cast mode (lyrics are baked into the MP4, so no custom Cast receiver UI is required). See the "Google Cast SDK Setup" section below.

See `.env.production.example` for a full description of every variable.

## Development

```bash
pnpm dev          # Start dev server on http://localhost:8080
pnpm test         # Run tests
pnpm test:watch   # Run tests in watch mode
pnpm lint         # Lint code
pnpm build        # Production build
```

## Database Migrations

```bash
psql "$SOW_DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS vector;'
npx drizzle-kit push       # Push schema changes to DB
npx drizzle-kit generate   # Generate migration files
npx drizzle-kit migrate    # Run pending migrations
```

## Routes

| Path | Description |
|------|-------------|
| `/login` | User login |
| `/register` | User registration |
| `/songsets` | Songset list |
| `/songsets/[id]` | Songset detail |
| `/songsets/[id]/render` | Render configuration |
| `/songsets/[id]/play` | Playback view |
| `/songsets/[id]/play/controller` | Controller player (Cast + Presentation API fallback) |
| `/songsets/[id]/play/projection` | Second-screen lyrics projection |
| `/share/[token]` | Public shared player |
| `/share/[token]/play/audio` | Shared audio playback |
| `/share/[token]/play/projection` | Shared projection playback |
| `/settings` | User settings |

## API Summary

- `GET /api/songs`, `GET /api/songs/[id]`, `GET /api/songs/search`, `GET /api/songs/albums`
  Auth required. Full-text search uses Postgres tsvector across title, pinyin, composer, lyricist, and album fields with relevance ranking. Returns only songs with at least one published recording for app callers.
- `POST /api/songs/search/semantic`
  Auth required. Find songs similar to a given recording by looking up its pre-computed embedding via pgvector. Requires `recordingId` in the request body.
- `GET|POST|PATCH|DELETE /api/songsets`, `/api/songsets/[id]`, `/api/songsets/[id]/items`, `/api/songsets/[id]/items/reorder`
  Auth required. Songset CRUD, item editing, and reorder, all ownership-scoped.
- `POST /api/render-jobs`, `GET /api/render-jobs/[id]`, `DELETE /api/render-jobs/[id]`, `GET /api/render-jobs/[id]/events`, `GET /api/render-jobs/[id]/artifact-sizes`
  Auth required. Render creation, status lookup, cancellation, SSE progress streaming, and artifact size queries.
- `GET|POST /api/signed-url`
  Auth required. Signs published source recordings by `hashPrefix` or the caller's own render job artifacts by `renderJobId`.
- `POST /api/transitions/preview`
  Auth required. Generates signed URL for transition audio preview.
- `GET|DELETE /api/offline/cache`
  Auth required. Returns artifact URLs for offline caching or invalidates cached metadata for a completed render job.
- `GET|PUT /api/settings`
  Auth required. Reads and writes per-user transition/video/offline defaults.
- `GET|POST|DELETE /api/lyrics/marks`, `GET|PUT|DELETE /api/lyrics/overrides`
  Auth required. Stores lyric review marks and per-user LRC overrides.
- `POST /api/share`, `GET /api/share`, `DELETE /api/share/[token]`
  Auth required. Creates, lists, and revokes shares. Active shares are capped at 20 per user.
- `GET /api/share/[token]`
  Public. Validates a share token and returns signed playback URLs.

## Architecture

- **Framework**: Next.js 16 (App Router)
- **ORM**: Drizzle ORM with PostgreSQL (Neon serverless)
- **Auth**: Better Auth
- **Storage**: Cloudflare R2
- **Render Queue**: AWS SQS (render jobs are enqueued, not run inline)
- **Render Worker**: AWS Lambda container (Python, processes jobs from SQS)

## Deployment (Vercel Pro + AWS Lambda)

### Web App (Vercel)

1. Connect the repository to a Vercel project.
2. Set the **Root Directory** to `delivery/webapp/`.
3. Vercel auto-detects Next.js; `vercel.json` supplies the rest of the config.
4. Add all environment variables from `.env.production.example` in:
   Vercel Dashboard → Project Settings → Environment Variables

Render jobs are enqueued to SQS and processed by an AWS Lambda worker.
The `/api/render-jobs` endpoints only create jobs and return status, so
`maxDuration: 60` is sufficient (set in `vercel.json`). Fluid Compute is
no longer required on render routes since long-running encoding happens in
the Lambda worker, not in the Vercel function.

### Lambda Worker Deployment

The render worker is a Python container deployed to AWS Lambda via private ECR:

1. **Build the Docker image** from `delivery/render-worker/Dockerfile`:
   ```bash
   cd delivery/render-worker
   docker build -t sow-render-worker .
   ```

2. **Push to AWS ECR**:
   ```bash
   aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin \
     762288208920.dkr.ecr.us-west-2.amazonaws.com

   docker tag sow-render-worker:latest \
     762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest

   docker push 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
   ```

3. **Update the Lambda function** to use the new image:
   ```bash
   aws lambda update-function-code \
     --function-name sow-render-worker \
     --image-uri 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
   ```

> **Note:** AWS Lambda requires container images to be hosted in a **private ECR repository**. Public ECR image URIs are not accepted when creating or updating a Lambda function.

This is automated in the GitHub Actions deploy workflow (`.github/workflows/deploy.yml`).

### SQS Queue Setup

Create the SQS queue and dead-letter queue (DLQ) in AWS:

1. **Create the DLQ**:
   ```bash
   aws sqs create-queue --queue-name sow-render-jobs-dlq
   ```

2. **Create the main queue** with a redrive policy pointing to the DLQ:
   ```bash
   aws sqs create-queue --queue-name sow-render-jobs \
     --attributes '{
       "RedrivePolicy": "{\"deadLetterTargetArn\":\"arn:aws:sqs:us-east-1:<account-id>:sow-render-jobs-dlq\",\"maxReceiveCount\":\"3\"}",
       "VisibilityTimeout": "900"
     }'
   ```

   - **Visibility timeout**: 900 seconds (15 minutes) — must exceed the maximum
     expected render duration so the message is not re-delivered while the
     Lambda is still processing it.
   - **maxReceiveCount**: 3 — after 3 failed processing attempts, the message
     is moved to the DLQ for investigation.

3. **Configure the Lambda event source** to read from the queue:
   ```bash
   aws lambda create-event-source-mapping \
     --function-name sow-render-worker \
     --batch-size 1 \
     --event-source-arn arn:aws:sqs:us-east-1:<account-id>:sow-render-jobs
   ```

### Preview Deployments

Preview deployments are enabled for all branches via `vercel.json`:
```json
"git": { "deploymentEnabled": { "main": true, "*": true } }
```
Each preview branch gets its own URL (e.g. `your-app-git-branch-name.vercel.app`).

Preview deployments share the production environment variables unless preview-scoped values are configured. Cast features in a preview deployment use the Default Media Receiver (no per-environment registration required); set `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` only if you need a custom receiver ID pointing at the preview URL.

### Google Cast SDK Setup

v3 uses Google's **Default Media Receiver** as the only supported Cast mode. The
lyrics are baked into the rendered MP4 (rendered by the Python worker with
H.264 + AAC + `+faststart`), so the receiver needs no custom UI — it streams a
single MP4 from R2 directly. A Presentation API browser-projection fallback is
retained for developer-only second-screen testing; **production guidance is
Cast on Android/Chrome or AirPlay to Apple TV**.

1. Go to [https://cast.google.com/publish](https://cast.google.com/publish).
2. Register your **Cast test devices** by serial number under
   **Device registration**. Only whitelisted devices can cast during dev/staging.
3. Set `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` to the 8-character Default Media
   Receiver ID from Google's published Default Receiver constant (per
   environment, for dev / staging / prod), **or** leave it unset: when unset,
   the Web Sender SDK falls back to Google's built-in Default Media Receiver
   constant.
4. Production launch gate: submit the receiver for Cast review via the Cast SDK
   Developer Console → your app → **Submit for Approval**. Approvals typically
   take 2–4 weeks. Until approved, only whitelisted devices work.

#### Cast playback constraints (v3)

- **Receiver fetches the MP4 directly from R2** — the TV receiver only hits R2,
  never the webapp. The logged-in phone mints a presigned R2 URL (4-hour expiry
  for cast/share playback via the `cast=true` flag on `/api/signed-url`, or via
  `/api/share/[token]`) and hands the URL to the TV. Phone + Chromecast must be
  on the same LAN.
- **4-hour signed URL expiry** — `/api/signed-url?cast=true` and
  `/api/share/[token]` mint the MP4 at 14400s. This covers a full worship set
  plus setup slack; services longer than ~3h40m require a deliberate
  stop/re-cast before the URL expires (latest-wins buffering keeps the current
  session running while a new URL is minted).
- **MP4 must be H.264 + AAC + `+faststart`** — enforced by the render worker
  (`video_engine.get_video_codec_args()` appends `-movflags +faststart`) and
  verified by the `test_mp4_cast_compatibility.py` ffprobe pipeline test
  (asserts H.264, AAC, and `moov` atom precedes `mdat`). R2 must respond with
  `Content-Type: video/mp4` and honor range requests.
- **Receiver fetches only R2** — the TV never authenticates against the webapp;
  the phone's session owns the render job (songset path) or uses the public
  share token (share path).
- **iPhone web does not support Chromecast** — the Web Sender SDK is
  Android-Chrome-only. iPhones display an AirPlay-to-Apple-TV hint with a link
  to docs; native iOS sender app is future work.
- **Phone + Chromecast same LAN** — guest / captive-portal Wi-Fi may block
  discovery. Verify the receiver is discoverable on the same Wi-Fi/VLAN and is
  whitelisted in the Cast SDK Developer Console for dev/staging.
- **Pre-service network test (mandatory)** — before a live service, open the
  signed MP4 URL in a laptop browser on the same Wi-Fi/VLAN as the Chromecast
  and verify range-seek (forward/back 10s, reload). A failure means R2 is
  unreachable from that network and the Cast will show a black screen.
- **Presentation API = dev-only fallback** — the W3C Presentation API fallback
  (`src/hooks/usePresentation.ts`) opens a second browser screen with the
  controller's `/play/projection` route. It is intended only for
  developer/browser-projection debugging; do not rely on it for live service.

#### Legacy / future custom receiver

A **Custom Receiver** (separately registered receiver app ID pointing at
`/songsets/[id]/play/projection` or `/share/[token]/play/projection`) is **not
required for v3** and is only relevant if lyrics stop being baked into the MP4
and a custom on-receiver UI is reintroduced. To register one:

1. In the Cast SDK Developer Console, click **Add New Application** →
   **Custom Receiver**.
2. Enter the receiver URL:
   - Dev: `http://localhost:8080/songsets/<songset-id>/play/projection`
   - Staging/Preview: `https://your-app-preview.vercel.app/songsets/<songset-id>/play/projection`
   - Production: `https://your-app.vercel.app/songsets/<songset-id>/play/projection`
3. Save the generated App ID and set it as `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`
   in the matching environment.

Set the env var per environment, or omit it entirely and the Web Sender SDK uses
Google's Default Media Receiver constant.

See the Live-Service Go/No-Go Checklist below before any first live use.

### Live-Service Go/No-Go Checklist

Required before the first live use, on the same TV + network class used in
service. All items must pass.

1. **Network topology** — phone + TV on the same Wi-Fi/VLAN; no captive portal
   / guest isolation between them.
2. **Receiver discoverability** — TV/Chromecast discoverable from the phone on
   the same network + whitelisted in the Cast SDK Developer Console for
   dev/staging.
3. **Signed URL range-seek** — open the MP4 URL from a laptop browser on the
   same network and verify seek (forward/back 10s) + reload succeed. Failure ⇒
   R2 unreachable from that network ⇒ Cast black screen.
4. **MP4 compatibility on real TV** — freshly rendered MP4 (post-faststart)
   starts quickly on the TV, supports 10s range seek, chapter jump, lyric-line
   jump; `ffprobe` pipeline test passes (H.264 / AAC / moov-at-front).
5. **Transport on real TV** — play/pause, volume, mute (the mute bit, not a
   volume-zero command), chapter jump, lyric-line jump all driven from the
   phone.
6. **Disconnect resume** — resumes local playback from the extrapolated TV
   position; audio un-mutes; tap-to-resume renders if `video.play()` rejects.
   Verify by backgrounding the phone during cast, then disconnecting.
7. **Stale signaling** — when receiver status was silent for >60s before
   disconnect, the "Resume from TV position may be stale — tap to resume at
   \<time\>" prompt renders instead of silent auto-resume.
8. **Diagnostic UX** — on a no-Cast-devices network, tapping the disabled Cast
   button opens the bottom sheet with the 4 diagnostic lines (Android Chrome
   on HTTPS; same Wi-Fi/VLAN; receiver powered on + whitelisted; try opening
   the MP4 URL from this network).
9. **Rehearsal** — service-length rehearsal (≥60 min) on the same TV/network
   class with no URL expiry or receiver stalls.
10. **Telemetry** — `POST /api/log-client-error` is reachable from the phone,
    rate-limited (20 req/min per IP), and persists structured anonymized rows
    for one simulated `loadMedia` failure.
