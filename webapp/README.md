# Stream of Worship Web App

Web application for rendering worship music transitions with synchronized lyrics videos.

## Prerequisites

- Node.js 18+
- pnpm
- PostgreSQL database (Neon recommended)
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
| `/songsets/[id]/play/projection` | Second-screen lyrics projection |
| `/share/[token]` | Public shared player |
| `/settings` | User settings |

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

The render pipeline (FFmpeg audio + video encoding) can run for up to 13 minutes.
This requires **Vercel Pro** with `maxDuration: 800` set in `vercel.json` for the
`/api/render-jobs` endpoint. The Free/Hobby plan cap of 10/60 seconds is insufficient.

**Fluid Compute** is enabled for render functions (`"fluid": true` in `vercel.json`),
allowing a single function instance to handle multiple concurrent SSE connections and
progress queries without cold-starting a new instance for each poll.

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
   - **Dev**: your ngrok or local tunnel URL + `/cast-receiver`
   - **Staging/Preview**: `https://your-app-preview.vercel.app/cast-receiver`
   - **Production**: `https://your-app.vercel.app/cast-receiver`
3. Copy the generated 8-character App ID and set it as `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`
   in the matching Vercel environment.

#### Production Cast approval

Google requires review before a Cast receiver app can be used by the general public.
Submit via the Cast SDK Developer Console → your app → **Submit for Approval**.
Review typically takes 2–4 weeks. Until approved, only **whitelisted Cast devices**
(registered in the console by serial number) can use the receiver.
Dev and staging IDs are approved immediately and require no review.
