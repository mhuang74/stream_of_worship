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

pnpm --filter sow-marketing lint
pnpm --filter sow-marketing typecheck
```

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

Serve the static export (`out/`) at `https://streamofworship.com` from any static host. Set `NEXT_PUBLIC_APP_URL` if the app domain differs from the default. Infra provisioning is tracked in issue #213.
