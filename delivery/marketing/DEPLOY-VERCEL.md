# Deploy Marketing Site to Vercel

The marketing site (`delivery/marketing/`, package `sow-marketing`) is a standalone
static Next.js export (`output: "export"`) served from `https://streamofworship.com`.
It has no database, no auth, and no secrets, so it uses Vercel's **native Git
integration** — unlike the webapp, there is no GitHub Actions pipeline, no Drizzle
migration step, and no deploy hook. A `git push` to `main` is the entire deployment.

## Auto-Deploy Behavior

| Action | Result |
|---|---|
| Push to `main` with changes under `delivery/marketing/` | Production deploy to `https://streamofworship.com` |
| Push to any other branch | Preview deploy (unique URL, also posted on the PR) |
| Push to `main` with no changes under `delivery/marketing/` | No-op deploy (no diff to build, no domain change) |

Auto-deploy is enabled for every branch in `delivery/marketing/vercel.json`:

```json
"git": {
  "deploymentEnabled": {
    "main": true,
    "*": true
  }
}
```

## Prerequisites

- Vercel CLI: `pnpm add -g vercel` (or use `npx vercel` per-command)
- `vercel login`
- Node.js 20.9+
- pnpm

## Step 1: Create & Link a Second Vercel Project

One Vercel project per site. The webapp project (`stream-of-worship-webapp`) must
not serve the marketing pages. From the project root:

```bash
mkdir -p delivery/marketing/.vercel
vercel link --project stream-of-worship-marketing --cwd delivery/marketing
```

When prompted, set the **Root Directory** to `delivery/marketing/` (or set it in
the dashboard under **Settings → General → Root Directory**). Linking creates
`delivery/marketing/.vercel/` — already covered by the root `.gitignore` (`.vercel/`).

**Dashboard alternative:** Vercel dashboard → **Add New → Project** → import the repo
→ set **Root Directory** to `delivery/marketing/` → the project is created with the
settings below.

`delivery/marketing/vercel.json` configures the project:

| Setting | Value | Purpose |
|---|---|---|
| `framework` | `nextjs` | Auto-detected, explicit for safety |
| `buildCommand` | `pnpm build` | Runs the pnpm workspace build, producing `out/` |
| `installCommand` | `pnpm install --frozen-lockfile` | Deterministic workspace install |
| `git.deploymentEnabled` | `main: true`, `*: true` | Push to `main` = production deploy; any other branch = preview deploy |

## Step 2: Environment Variables (Optional)

The site builds with sensible defaults without any variables — skip this step unless
you need to override the URLs:

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_APP_URL` | `https://app.streamofworship.com` | Webapp base URL for register/login CTAs and the signup form's `${APP_URL}/api/capture-email` POST |
| `NEXT_PUBLIC_SITE_URL` | `https://streamofworship.com` | Canonical marketing base URL (sitemap) |

Because it's a static export, `NEXT_PUBLIC_*` values are baked in at build time —
change them in **Settings → Environment Variables** and redeploy. No secrets ever
belong on the marketing site.

### Preview environment: point the signup form at the webapp preview

Production needs no variables (defaults are correct). In **Preview**, set one
variable so the lead-capture form talks to the webapp preview instead of
production:

| Variable | Scope | Preview value |
|---|---|---|
| `NEXT_PUBLIC_APP_URL` | Preview | The webapp's stable preview branch domain (e.g. `https://qa-app.streamofworship.com`) — random per-deployment URLs cannot work: `NEXT_PUBLIC_*` is baked at build time and the webapp's `SOW_MARKETING_ORIGINS` allowlist is exact-match |

On the webapp project, the matching origin must also be allowlisted: add the
marketing preview origin to `SOW_MARKETING_ORIGINS` at **Preview** scope (see
[`../webapp/DEPLOY-VERCEL.md`](../webapp/DEPLOY-VERCEL.md), "Preview Environments").
Without both sides, the browser blocks the form's POST at CORS preflight. R2,
database, and SQS are shared single-environment infrastructure — previews use the
same Neon database and R2 bucket as production by design.

`NEXT_PUBLIC_SITE_URL` can stay at its production default on previews.

## Step 3: Deploy & Verify

Trigger a deploy either by pushing:

```bash
git push origin main   # changes under delivery/marketing/ → production deploy
```

Or manually from `delivery/marketing/`:

```bash
vercel --prod
```

Post-deploy checks (replace `https://<project>.vercel.app` with your domain once
assigned):

1. `https://<project>.vercel.app/` renders the landing page.
2. `/about` and `/docs` resolve.
3. `/zh-Hant`, `/zh-Hant/about`, and `/zh-Hant/docs` serve the 繁體中文 mirror.
   Note: `/zh-Hant/` 308-redirects to `/zh-Hant` (trailing-slash normalization) —
   test with `curl -sL` to follow the redirect.
4. `robots.txt` and `sitemap.xml` are served.
5. CTA buttons link to `https://app.streamofworship.com`.

**Preview deploys:** push a non-`main` branch (or open a PR) and Vercel posts a
preview URL — useful for reviewing marketing copy before it goes live.

## Step 4: Custom Domain

1. In the marketing project: **Settings → Domains** → add `streamofworship.com`
   (apex) and `www.streamofworship.com`.
2. Add the DNS records Vercel shows; SSL is automatic.
3. Keep `app.streamofworship.com` assigned to the **webapp** project — the apex and
   `www` belong to marketing, the `app.` subdomain to the webapp.
4. If `NEXT_PUBLIC_SITE_URL` differs from the final domain, update it in
   **Settings → Environment Variables** and redeploy.

## Troubleshooting

| Symptom | Cause & Fix |
|---|---|
| Push to `main` doesn't deploy | Root Directory not set to `delivery/marketing/` (monorepo path filtering at project setup) |
| Build runs the webapp instead of marketing | Project linked to the wrong root — re-run `vercel link --cwd delivery/marketing` and confirm Root Directory |
| Env var change has no effect | `NEXT_PUBLIC_*` is baked at build time — any push to the branch triggers a rebuild, or redeploy manually from the dashboard |
| 404 on `/zh-Hant/about` via `curl` | Expected: `/zh-Hant/` 308-redirects to `/zh-Hant`. Use `curl -sL` to follow the redirect |

## Reference

- [`./vercel.json`](./vercel.json) — framework, build command, git deploy settings
- [`./README.md`](./README.md) — marketing site overview and local development
- [`../webapp/DEPLOY-VERCEL.md`](../webapp/DEPLOY-VERCEL.md) — webapp deploy (GitHub Actions, migration, deploy hook)
