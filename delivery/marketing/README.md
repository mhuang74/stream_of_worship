# Marketing Site (`sow-marketing`)

The public marketing site for Stream of Worship: landing, about, and docs pages. It owns `https://streamofworship.com`; the webapp lives at `https://app.streamofworship.com` and is login-first — unauthenticated visits redirect to `/login` (issue #213).

## Pages

| Path | Description |
|------|-------------|
| `/` | Landing page |
| `/about` | About |
| `/docs` | Docs (includes `#airplay` anchor targeted by the webapp ControllerPlayer) |
| `/zh-Hant/...` | 繁體中文 mirror of all three pages |

The language toggle uses plain links (no client-side locale state).

## Prerequisites

- Node.js 20.9+
- pnpm

## Development

```bash
# Install dependencies (from the repo root — pnpm workspace)
pnpm install

# Dev server on http://localhost:3001
pnpm --filter sow-marketing dev

# Production build — static export to out/
pnpm --filter sow-marketing build

# Unit tests (Vitest, jsdom)
pnpm --filter sow-marketing test

pnpm --filter sow-marketing lint
pnpm --filter sow-marketing typecheck
```

## Lead capture ("Sign up for Free Curated Songsets")

The hero and bottom CTA carry an inline email form (`src/components/SongsetSignupForm.tsx`) offering free curated songsets sent via email, instead of a Log In button; Sign in stays in the header nav. Submitting posts to `<NEXT_PUBLIC_APP_URL>/api/capture-email`, which stores the address in Brevo and emails a validation link (`/validated?token=…`); confirming marks the contact `VALIDATED` in Brevo and leads on to a pre-filled signup. The form is client-side only — this site stays a static export.

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `NEXT_PUBLIC_APP_URL` | `https://app.streamofworship.com` | Webapp base URL used by register/login CTAs |

No other env vars, no database, no secrets.

## SEO

- `public/robots.txt` allows all crawlers and points at the sitemap.
- `src/app/sitemap.ts` emits 6 URLs (`force-static` is required by the static export).
- `/zh-Hant/` 308-redirects to `/zh-Hant` (trailing-slash normalization) — use `curl -sL` when testing.

## Deployment

The site auto-deploys on Vercel via Git integration (see `vercel.json`):

- **Project:** `stream-of-worship-marketing` — Root Directory `delivery/marketing/`
- Push to `main` (with changes under `delivery/marketing/`) → production deploy to `https://streamofworship.com`
- Push to any other branch → preview deploy
- No environment variables or secrets required; optionally set `NEXT_PUBLIC_APP_URL` / `NEXT_PUBLIC_SITE_URL` in Vercel **Settings → Environment Variables** (baked in at build time — push any commit to the branch or redeploy manually to rebuild with the new values)

Full setup steps (project creation, custom domain) are in
[`./DEPLOY-VERCEL.md`](./DEPLOY-VERCEL.md).

The static export (`out/`) can also be served from any static host.
