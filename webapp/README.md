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

- `DATABASE_URL` — PostgreSQL connection string
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` — Cloudflare R2 credentials
- `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL` — Better Auth configuration
- `NEXT_PUBLIC_BASE_URL` — Base URL of the app (for share links)
- `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` — (optional) Google Cast SDK receiver app ID

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
psql "$DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS vector;'
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
| `/songsets/[id]/play/controller` | Controller player (Presentation API) |
| `/songsets/[id]/play/projection` | Second-screen lyrics projection |
| `/share/[token]` | Public shared player |
| `/share/[token]/play/audio` | Shared audio playback |
| `/share/[token]/play/projection` | Shared projection playback |
| `/settings` | User settings |

## API Summary

- `GET /api/songs`, `GET /api/songs/[id]`, `GET /api/songs/search`, `GET /api/songs/albums`
  Auth required. Returns only songs with at least one published recording for app callers.
- `POST /api/songs/search/semantic`
  Auth required. Natural-language search backed by pgvector plus the local embedding model.
- `GET|POST|PATCH|DELETE /api/songsets`, `/api/songsets/[id]`, `/api/songsets/[id]/items`, `/api/songsets/[id]/items/reorder`
  Auth required. Songset CRUD, item editing, and reorder, all ownership-scoped.
- `POST /api/render-jobs`, `GET /api/render-jobs/[id]`, `DELETE /api/render-jobs/[id]`, `GET /api/render-jobs/[id]/events`, `GET /api/render-jobs/[id]/artifact-sizes`
  Auth required. Render creation, status lookup, cancellation, SSE progress streaming, and artifact size queries.
- `GET|POST /api/signed-url`
  Auth required. Signs published source recordings by `hashPrefix` or the caller's own render job artifacts by `renderJobId`.
- `POST /api/embed`
  Auth required. Edge function for generating text embeddings (semantic search).
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
- **Rendering**: FFmpeg (audio mixing, video encoding)

## Deployment (Vercel Pro)

### Setup

1. Connect the repository to a Vercel project.
2. Set the **Root Directory** to `webapp/`.
3. Vercel auto-detects Next.js; `vercel.json` supplies the rest of the config.
4. Add all environment variables from `.env.production.example` in:
   Vercel Dashboard → Project Settings → Environment Variables

### Vercel Pro requirements

Render jobs are now enqueued to SQS and processed by an AWS Lambda worker.
The `/api/render-jobs` endpoints only create jobs and return status, so
`maxDuration: 60` is sufficient (set in `vercel.json`). Fluid Compute is
no longer required on render routes since long-running encoding happens in
the Lambda worker, not in the Vercel function.

### Preview Deployments

Preview deployments are enabled for all branches via `vercel.json`:
```json
"git": { "deploymentEnabled": { "main": true, "*": true } }
```
Each preview branch gets its own URL (e.g. `your-app-git-branch-name.vercel.app`).

To use Cast features in a preview deployment, register a separate Cast receiver
app ID pointing to the preview URL. See `.env.production.example` for details.

### Google Cast SDK Setup

1. Go to [https://cast.google.com/publish](https://cast.google.com/publish).
2. Register a **Custom Receiver** for each environment:
   - **Dev**: your ngrok or local tunnel URL + `/songsets/<songset-id>/play/projection`
   - **Staging/Preview**: `https://your-app-preview.vercel.app/songsets/<songset-id>/play/projection`
   - **Production**: `https://your-app.vercel.app/songsets/<songset-id>/play/projection`
3. Copy the generated 8-character App ID and set it as `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`
   in the matching Vercel environment.

For public share playback, the receiver route is `/share/<token>/play/projection`.

#### Production Cast approval

Google requires review before a Cast receiver app can be used by the general public.
Submit via the Cast SDK Developer Console → your app → **Submit for Approval**.
Review typically takes 2–4 weeks. Until approved, only **whitelisted Cast devices**
(registered in the console by serial number) can use the receiver.
Dev and staging IDs are approved immediately and require no review.
