# Deploy Webapp to Vercel

Step-by-step guide for deploying the Stream of Worship delivery/webapp to Vercel.

## Prerequisites

- [Vercel CLI](https://vercel.com/docs/cli) installed (`npm i -g vercel`)
- Node.js 18+ and pnpm installed
- Access to the GitHub repository
- Production environment credentials on hand (see [Reference](#reference-infrastructure--migration))

## Step 1: Create & Link Vercel Project

From the **project root** (not the `delivery/webapp/` directory):

```bash
vercel link
```

When prompted:

1. **Set up and deploy?** → Yes
2. **Which scope?** → Your team or personal account
3. **Link to existing project?** → No (first time) or Yes (if re-linking)
4. **Project name** → e.g. `stream-of-worship`
5. **Which directory is your code in?** → `delivery/webapp/`

> **Important:** The project lives inside a pnpm workspace monorepo. Vercel must set the **Root Directory** to `delivery/webapp/` so it finds `package.json`, `next.config.ts`, etc. If the CLI doesn't prompt for this, set it manually in the Vercel dashboard under **Settings → General → Root Directory**.

After linking, Vercel creates a `.vercel/` directory in `delivery/webapp/` (already gitignored).

## Step 2: Configure Environment Variables

Add all production environment variables in the Vercel dashboard under **Settings → Environment Variables**, or via the CLI:

```bash
vercel env add SOW_DATABASE_URL production
vercel env add SOW_R2_ENDPOINT_URL production
# ... repeat for each variable
```

### Variable Reference

| Variable | Scope | Description |
|---|---|---|
| `SOW_DATABASE_URL` | Server | Neon PostgreSQL connection string |
| `SOW_R2_ENDPOINT_URL` | Server | Cloudflare R2 endpoint URL |
| `SOW_R2_ACCESS_KEY_ID` | Server | R2 API token access key |
| `SOW_R2_SECRET_ACCESS_KEY` | Server | R2 API token secret key |
| `SOW_R2_BUCKET` | Server | R2 bucket name (e.g. `stream-of-worship-prod`) |
| `NEXT_PUBLIC_R2_PUBLIC_DOMAIN` | Client | Public R2 domain for direct streaming |
| `BETTER_AUTH_SECRET` | Server | 32+ char random secret (`openssl rand -base64 32`) |
| `BETTER_AUTH_URL` | Server | Deployed app URL (e.g. `https://your-app.vercel.app`) |
| `NEXT_PUBLIC_BASE_URL` | Client | Same as `BETTER_AUTH_URL` (embedded in client bundle) |
| `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` | Client | Google Cast receiver app ID. Leave unset to use Google's Default Media Receiver (the v3 default). One per environment, or empty. |
| `SOW_AWS_REGION` | Server | AWS region for SQS (e.g. `us-east-1`) |
| `SOW_SQS_QUEUE_URL` | Server | SQS queue URL for render jobs |
| `SOW_AWS_ACCESS_KEY_ID` | Server | AWS IAM access key (SQS SendMessage) |
| `SOW_AWS_SECRET_ACCESS_KEY` | Server | AWS IAM secret key |
| `SOW_SQS_ENDPOINT_URL` | Server | Leave empty in production |
| `SOW_RENDER_WORKER_MODE` | Server | Set to `sqs` in production |
| `SOW_RENDER_WORKER_REST_URL` | Server | Leave empty in production |
| `UPSTASH_REDIS_REST_URL` | Server | Upstash Redis REST URL for `POST /api/log-client-error` rate limiting (optional; allow-all fallback when unset) |
| `UPSTASH_REDIS_REST_TOKEN` | Server | Upstash Redis REST token (optional; recommend setting in production) |

**Key points:**

- Variables prefixed `NEXT_PUBLIC_` are embedded in the client bundle at build time — they must be set **before** deploying.
- All other variables are server-only and are not exposed to the browser.
- Set variables for each environment separately: **Production**, **Preview**, and **Development**. At minimum, configure **Production**.

### Quick CLI Bulk Add

```bash
# From delivery/webapp/ directory — add all production vars at once
vercel env pull .env.production.local  # if project already has vars set

# Or set individually:
vercel env add SOW_DATABASE_URL production <<< "postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require"
vercel env add BETTER_AUTH_SECRET production <<< "$(openssl rand -base64 32)"
vercel env add BETTER_AUTH_URL production <<< "https://your-app.vercel.app"
vercel env add NEXT_PUBLIC_BASE_URL production <<< "https://your-app.vercel.app"
vercel env add SOW_RENDER_WORKER_MODE production <<< "sqs"
```

## Step 3: Deploy

### Option A: Git Push (Recommended)

The project's `vercel.json` already enables automatic deployments:

```json
"git": {
  "deploymentEnabled": {
    "main": true,
    "*": true
  }
}
```

Pushing to `main` triggers a production deploy. Pushing any other branch triggers a preview deploy.

```bash
git push origin main
```

Monitor the deploy at `https://vercel.com/dashboard` or in the CLI:

```bash
vercel inspect <deployment-url>
```

### Option B: CLI Deploy

```bash
# From delivery/webapp/ directory
vercel --prod
```

### What `vercel.json` Configures

The existing `vercel.json` handles:

| Setting | Value | Purpose |
|---|---|---|
| `framework` | `nextjs` | Auto-detected, explicit for safety |
| `buildCommand` | `pnpm build` | Uses pnpm, not npm |
| `installCommand` | `pnpm install --frozen-lockfile` | Deterministic installs |
| `functions.*.maxDuration` | `60` | Render job API routes get 60s timeout |
| `regions` | `iad1` | Deploy to US East (same as SQS/R2) |
| `headers` | Cache rules | No-cache for projection pages, immutable for static assets |

No changes to `vercel.json` are needed for a standard deployment.

## Step 4: Post-Deploy Verification

After the first successful deploy, verify each subsystem:

### Authentication

1. Visit `https://your-app.vercel.app` and sign up / log in.
2. Check that the session cookie is set (`Secure; HttpOnly; SameSite=Lax`).
3. If login fails, verify `BETTER_AUTH_URL` matches the deployed URL exactly (no trailing slash).

### R2 File Access

1. Create a songset and attempt to play audio.
2. If audio doesn't load, check `NEXT_PUBLIC_R2_PUBLIC_DOMAIN` is set and the R2 bucket has public access enabled (or signed URLs are working via `/api/signed-url`).

### SQS Render Jobs

1. Trigger a render (e.g. generate audio for a songset).
2. Check Vercel function logs for SQS `SendMessage` success.
3. Verify the Lambda render worker picks up and processes the job.

### Google Cast (Default Media Receiver — v3)

v3 uses Google's **Default Media Receiver** as the only supported Cast mode.
Lyrics are baked into the rendered MP4 (H.264 + AAC + `+faststart`), so no
custom on-receiver UI is required.

1. Leave `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` unset for the supported v3 path.
   The Web Sender SDK falls back to Google's built-in Default Media Receiver
   constant.
2. Set `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` only when intentionally testing a
   custom receiver app ID from the [Cast SDK Developer Console](https://cast.google.com/publish).
   The env var is the operator's responsibility, set in Vercel Project
   Settings → Environment Variables. Whitespace-only values are ignored by the
   app, but a real custom ID always wins over the Default Media Receiver.
3. The logged-in phone mints a presigned R2 URL with a 4-hour expiry for Cast
   playback. `POST /api/signed-url?cast=true` (songset ownership path,
   session-required) or `/api/share/[token]` (public share path) mint the MP4
   at 14400s; the TV receiver fetches the MP4 directly from R2 and never hits
   the webapp. Services longer than ~3h40m require a deliberate stop/re-cast
   before URL expiry.
4. Custom receiver only: register Cast test devices by serial number under
   **Device registration**. Until a custom receiver is approved, only
   whitelisted devices can launch it. This is not required for the Default
   Media Receiver path.

#### Rendered MP4 requirements (enforced by the render worker)

The render worker (`delivery/render-worker/`) emits H.264 + AAC + `+faststart`
MP4s (`video_engine.get_video_codec_args()` appends `-movflags +faststart`),
placing the `moov` atom at the front so TV hardware can start decoding before
the full file is fetched. The `test_mp4_cast_compatibility.py` ffprobe pipeline
test asserts: video codec `h264`, audio codec `aac`, `moov` precedes `mdat`,
upload `content_type` remains `video/mp4`. R2 must respond with
`Content-Type: video/mp4` and honor range requests.

#### See the Live-Service Go/No-Go Checklist (in `README.md`) before first live use.

Runbook reminder: phone + TV on the same Wi-Fi/VLAN; receiver fetches MP4
directly from R2 (logged-in phone mints the URL); iPhone web does not support
Chromecast — use AirPlay to Apple TV instead.

## Step 5: Custom Domain (Optional)

1. In the Vercel dashboard, go to **Settings → Domains**.
2. Add your custom domain (e.g. `app.streamofworship.com`).
3. Add the DNS records shown by Vercel to your domain's DNS provider.
4. Wait for SSL certificate provisioning (usually automatic within minutes).
5. Update these environment variables to use the custom domain:
   - `BETTER_AUTH_URL` → `https://app.streamofworship.com`
   - `NEXT_PUBLIC_BASE_URL` → `https://app.streamofworship.com`
6. Redeploy for `NEXT_PUBLIC_BASE_URL` to take effect (it's baked into the client bundle).

## Troubleshooting

### Build fails: "Root Directory" not set

If Vercel builds from the repo root instead of `delivery/webapp/`, the build will fail because `package.json` isn't found. Fix in **Settings → General → Root Directory** → set to `delivery/webapp/`.

### Build fails: pnpm not found

Ensure the project has a `pnpm-lock.yaml` committed. Vercel auto-detects pnpm when a lockfile is present. If missing:

```bash
cd delivery/webapp && pnpm install && git add pnpm-lock.yaml && git commit -m "add pnpm lockfile"
```

### Function timeout on render job routes

The `vercel.json` sets `maxDuration: 60` for render job API routes. On the **Hobby plan**, the max is 10 seconds. Upgrade to **Pro** for 60s serverless functions, or reduce to `10` and rely on the async SQS pattern (the API route only enqueues, it doesn't wait for render completion).

### Auth cookies not working

- `BETTER_AUTH_URL` must match the deployed URL exactly (including `https://`).
- On Vercel, `NODE_ENV` is always `production`, so `useSecureCookies` is automatically enabled in `src/lib/auth.ts:28`.
- If using a custom domain, ensure `BETTER_AUTH_URL` is updated before redeploying.

### `NEXT_PUBLIC_` variables not updating

These are embedded at **build time**. Changing them in the Vercel dashboard requires a redeploy:

```bash
vercel --prod
```

### Preview deploys use wrong environment

Preview deploys use the **Preview** environment variables. If not set, they fall back to Development. Configure Preview-specific values in **Settings → Environment Variables** (e.g. a staging R2 bucket, a different Cast receiver app ID).

---

## Reference: Infrastructure & Migration

These systems are already provisioned. This section documents the setup for reference.

### Neon PostgreSQL

- **Console:** https://console.neon.tech
- The delivery/webapp uses Drizzle ORM with the `@neondatabase/serverless` driver.
- Connection string format: `postgresql://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require`
- The schema is defined in `src/db/schema.ts` and includes tables for users, sessions, accounts, songs, songsets, render jobs, and more.

### Database Migration

Before the first deploy (or after schema changes), push the schema to the Neon database:

```bash
# Set SOW_DATABASE_URL to the production Neon connection string
export SOW_DATABASE_URL="postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require"

# From delivery/webapp/ directory
npx drizzle-kit push
```

For subsequent schema changes, generate and apply migrations:

```bash
npx drizzle-kit generate   # Generate migration SQL files
npx drizzle-kit migrate    # Apply pending migrations
```

### Cloudflare R2

- **Console:** https://dash.cloudflare.com → R2
- The delivery/webapp stores rendered audio (MP3) and video (MP4) files in R2.
- Required credentials: `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`, `SOW_R2_BUCKET`.
- `NEXT_PUBLIC_R2_PUBLIC_DOMAIN` must point to a public R2 domain (custom domain or `r2.dev` public URL).
- Signed URLs are generated server-side via `/api/signed-url` as a fallback when no public domain is configured.

### AWS SQS

- **Console:** https://console.aws.amazon.com/sqs
- The delivery/webapp enqueues render jobs to SQS; an AWS Lambda container worker processes them.
- Required credentials: `SOW_AWS_REGION`, `SOW_SQS_QUEUE_URL`, `SOW_AWS_ACCESS_KEY_ID`, `SOW_AWS_SECRET_ACCESS_KEY`.
- IAM user needs minimum permissions: `sqs:SendMessage` on the render jobs queue ARN.
- `SOW_RENDER_WORKER_MODE` must be `sqs` in production. The `rest` mode is for local development only.
