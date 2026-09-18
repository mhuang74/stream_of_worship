# Separate marketing site from webapp

## Context

The public (logged-out) marketing surface — signed-out landing at `/`, `/about`, `/docs` — lives inside the Next.js webapp (`delivery/webapp`). Goal: extract it into an independently deployable static site so it can evolve separately from the logged-in app. Decisions made with the user:

- Marketing owns **https://streamofworship.com** (root); webapp moves to **https://app.streamofworship.com** (matches existing email sender domain in `src/lib/email/client.ts`).
- Same repo, new pnpm workspace `delivery/marketing`; Next.js **static export**; deploys as a second Vercel project (wiring done outside this repo).
- Pages moved: landing (with existing demo video, YouTube ID `4X4RQxU7SlU`), `/about`, `/docs`. Bilingual: English at root paths, 繁體中文 under `/zh-Hant/…`, language toggle links between them, no auto-detect.
- Logged-out visitor hitting the app: proxy redirects `/` → `/login`. Minimal app-domain surface; in-app `/about` and `/docs` links point out to the marketing domain.
- App domain robots: disallow all crawlers; sitemap.xml removed from the app. Marketing site gets its own robots.txt + sitemap.

## Approach

Two independent phases. **Do Phase A first** (it copies copy/content out of the webapp before Phase B deletes it).

### Phase A — new marketing workspace `delivery/marketing`

1. Add `delivery/marketing` to `packages` in root `pnpm-workspace.yaml`.
2. Create `delivery/marketing/package.json`: name `sow-marketing`, private, `packageManager: pnpm@10.24.0`, scripts: `dev` (`next dev -p 3001`), `build` (`next build`), `lint` (`eslint`), `typecheck` (`tsc --noEmit`). Deps mirror webapp versions: `next@16.2.6`, `react`/`react-dom` (same majors as `delivery/webapp/package.json`), `tailwindcss@^4`, `@tailwindcss/postcss@^4`, `typescript`, `@types/react`, `@types/react-dom`, `@types/node`. No auth/DB/R2 deps.
3. Create `delivery/marketing/next.config.ts`:
   ```ts
   import type { NextConfig } from "next";
   const nextConfig: NextConfig = { output: "export", images: { unoptimized: true } };
   export default nextConfig;
   ```
4. Create `delivery/marketing/tsconfig.json`, `postcss.config.mjs`, `eslint.config.mjs` — copy from `delivery/webapp` and adjust paths (no `@/*` changes needed if `src/` layout is kept).
5. Copy `delivery/webapp/src/app/globals.css` **verbatim** into `delivery/marketing/src/app/globals.css` (Tailwind v4 + shadcn tokens + `.gradient-hero`). This guarantees the ported pages look identical.
6. Create `delivery/marketing/src/lib/urls.ts`:
   ```ts
   export const APP_URL = process.env.NEXT_PUBLIC_APP_URL ?? "https://app.streamofworship.com";
   export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://streamofworship.com";
   ```
   (Build-time inlined by static export; that is acceptable — content changes require rebuild anyway.)
7. Create `delivery/marketing/src/messages.ts` — a plain typed record `{ en: Record<Key,string>; "zh-Hant": Record<Key,string> }` (same shape philosophy as webapp's `MessageBundle`, but no bundle infrastructure — no equivalent needed). Populate by copying these keys verbatim from `delivery/webapp/src/lib/i18n/messages/core.ts` (both locales, lines ~46-77 en / ~250-290 zh): all `home.signedOut.*`, all `about.*`, `brand.name`, `nav.about`. Copy all `docs.*` keys from `delivery/webapp/src/lib/i18n/messages/docs.ts`. Add two new keys: `"nav.docs": "Docs"` / `"nav.docs": "使用說明"`, and `"docs.footerNote"` not needed — skip. Also `"about.backToHome"`/nav label `"nav.home": "Home"` / `"首頁"`.
8. Port pages. Structure (`src/app/`):
   - `layout.tsx` — minimal: imports `./globals.css`, `<html lang>` is per-page concern (static export renders both trees); simplest decision-complete approach: root layout has `<html lang="en">`; the zh-Hant tree sets `<html lang="zh-Hant">` via its own nested layout `src/app/zh-Hant/layout.tsx` wrapping content in a `<div lang="zh-Hant">` (html lang per-route isn't possible with one root layout; a div-level `lang` attribute is sufficient for a static marketing site).
   - `page.tsx` — English landing: port `delivery/webapp/src/app/page/PublicLanding.tsx` (hero, feature grid, how-it-works steps, the static CSS TV mockup, the demo-video section with the existing `https://www.youtube-nocookie.com/embed/4X4RQxU7SlU` iframe, footer). Replace `t(locale, key)` with the local `t(key)` from `src/messages.ts`. CTA links: `href={`${APP_URL}/register`}` and `href={`${APP_URL}/login`}`.
   - `about/page.tsx` — port `delivery/webapp/src/app/about/page.tsx`; final CTA links to `${APP_URL}/register`.
   - `docs/page.tsx` — port `delivery/webapp/src/app/docs/page.tsx`. **Must keep `id="airplay"` anchor** (in-app controller links to `/docs#airplay`).
   - `zh-Hant/page.tsx`, `zh-Hant/about/page.tsx`, `zh-Hant/docs/page.tsx` — same components with the zh-Hant dictionary.
   - Shared `src/components/SiteHeader.tsx` (brand + nav: Home, About, Docs links; language toggle linking to the counterpart locale path — plain `<Link>`s) and `src/components/SiteFooter.tsx` (brand name + copyright line; **no BuildStamp** — static export has no build-stamp infra and it is not load-bearing).
   - All internal nav links locale-relative (`/about` inside en pages, `/zh-Hant/about` inside zh pages).
9. SEO: create `delivery/marketing/public/robots.txt`:
   ```
   User-agent: *
   Allow: /
   Sitemap: https://streamofworship.com/sitemap.xml
   ```
   Create `delivery/marketing/src/app/sitemap.ts` returning the six URLs (`/`, `/about`, `/docs`, `/zh-Hant/`, `/zh-Hant/about`, `/zh-Hant/docs`) based on `SITE_URL`. Copy `delivery/webapp/src/app/favicon.ico` and `apple-icon.png` into `delivery/marketing/src/app/`.
10. Build proof (see Verification): `pnpm install` then `pnpm --filter sow-marketing build` must emit `delivery/marketing/out/` with the full static tree.

### Phase B — strip marketing surface from webapp

Order matters: each step keeps `pnpm --filter sow-webapp test` compilable.

1. **`delivery/webapp/src/app/page.tsx`**: remove the `PublicLanding` branch. New behavior: signed-out → `redirect("/login")` (import from `next/navigation`; the proxy also redirects, this is the server-render belt-and-braces). Signed-in path unchanged (dashboard via `HomePageClient`). Remove the `PublicLanding` import; keep `resolveUserLocale`, dashboard queries, `HomePageClient`.
2. **Delete** `delivery/webapp/src/app/page/` (contains `PublicLanding.tsx`, `BuildStamp.tsx`, `HomePageClient.tsx` is NOT there — verify: `HomePageClient.tsx` lives in `src/app/page/`? It is imported as `./page/HomePageClient`. So `HomePageClient.tsx` **moves** to `src/app/HomePageClient.tsx` and `page.tsx` import updates to `./HomePageClient`. Nothing else imports `@/app/page/...` — verify with grep before deleting.
3. **Delete** `delivery/webapp/src/app/about/` and `delivery/webapp/src/app/docs/`.
4. **`delivery/webapp/src/proxy.ts`**: `PUBLIC_PATHS` → remove `"/"`, `"/about"`, `"/docs"`. Result: `["/login", "/register", "/forgot-password", "/reset-password", "/api/auth", "/api/health", "/share", "/api/share", "/sw.js", "/sw-artifact-serving.js"]`. Keep the projection-suffix rule and all comments adjusted minimally.
5. **i18n cleanup** in `delivery/webapp/src/lib/i18n/`:
   - Delete `messages/docs.ts`; remove the `docsBundle` import and its entry from the `mergeMessages(...)` call in `messages.ts`.
   - In `messages/core.ts` remove all `home.signedOut.*` and `about.*` keys from BOTH locale maps. **Keep** `nav.about` (still used) and `brand.name`.
   - Grep `home.signedOut` / `"about.` / `docs\.` across `src/` after removal to catch stragglers (e.g. `src/test/lib/i18n/messages.test.ts` if it pins keys).
6. **Marketing URL helper** — new `delivery/webapp/src/lib/marketing-url.ts`:
   ```ts
   import type { Locale } from "@/lib/i18n/messages";
   export function getMarketingUrl(locale: Locale): string {
     const base = process.env.NEXT_PUBLIC_MARKETING_URL ?? "https://streamofworship.com";
     return locale === "zh-Hant" ? `${base}/zh-Hant` : base;
   }
   ```
   Add `NEXT_PUBLIC_MARKETING_URL=https://streamofworship.com` to `delivery/webapp/.env.example` and `.env.production.example` (next to `NEXT_PUBLIC_BASE_URL`).
7. **In-app link cutover** (all point at marketing domain, locale-aware):
   - `src/components/layout/Header.tsx` (~line 82, signed-out branch): `href="/about"` → `href={`${getMarketingUrl(locale)}/about`}`. Header is a client component using `t(...)` from LocaleProvider — obtain locale the same way the component already does for `t` (`useLocale()` from `@/contexts/LocaleContext`).
   - `src/components/layout/BottomNav.tsx` (~line 42, signed-out branch): same change.
   - `src/components/play/ControllerPlayer.tsx` (~line 1290): `href="/docs#airplay"` → `href={`${getMarketingUrl(locale)}/docs#airplay`}` (keep `target="_blank" rel="noreferrer"`).
   - Grep `"/about"` and `"/docs"` across `src/` (excluding tests) to confirm no other callers.
8. **Robots/sitemap**: delete `src/app/sitemap.ts`. Rewrite `src/app/robots.ts` to `rules: { userAgent: "*", disallow: "/" }` with no sitemap field (update its doc comment: app subdomain has no crawlable public content; share links are unlisted per-user URLs).
9. **Delete `src/lib/siteUrl.ts`** — its only consumers were `sitemap.ts` and `robots.ts`. Verify with `grep -rn "siteUrl" src/` (share.ts has its own `resolvePublicOrigin` and is unaffected).
10. **Tests** (update, don't pad — every change reflects a real contract change):
    - `src/test/app/pages.test.tsx`: remove the `DocsPage` describe. `HomePage` signed-out tests: the page now redirects — mock `next/navigation`'s `redirect` and assert it was called with `/login` (signed-in dashboard rendering is already covered by `HomePageClient.test.tsx`).
    - Delete `src/test/app/home/PublicLanding.test.tsx`.
    - `src/test/proxy.test.ts`: locale-cookie tests currently exercise public path `/` — repoint those requests to `/login` (still public, same `withAutoLocaleCookie` behavior). The "still redirects an unauthenticated app path" test can now also use `/` as the redirecting path — add one case asserting unauthenticated `/` → 307.
    - `src/test/app/header-avatar.test.tsx` (~line 90) and any `src/components/layout/Header`/`BottomNav` tests asserting `href="/about"`: assert the marketing URL instead (`https://streamofworship.com/about` — set `NEXT_PUBLIC_MARKETING_URL` in test setup or assert the default).
    - Run the full suite and fix any remaining failing references surfaced (e.g. accessibility tests referencing removed pages).

## Critical files & anchors

- `delivery/webapp/src/proxy.ts` — `PUBLIC_PATHS` constant (~line 6) and the `proxy()` redirect logic; the gate for everything public.
- `delivery/webapp/src/app/page.tsx` — session branch to remove; `HomePageClient` import path changes.
- `delivery/webapp/src/app/page/PublicLanding.tsx` — the content source to port (hero/features/steps/mockup/video iframe ~line 111-126).
- `delivery/webapp/src/lib/i18n/messages/core.ts` — `home.signedOut.*` (~46-77 en, ~250-290 zh) and `about.*` (~88-98, ~362-372) keys to extract then delete.
- `delivery/webapp/src/components/layout/Header.tsx` (~82), `BottomNav.tsx` (~42), `ControllerPlayer.tsx` (~1290) — the three link cutovers.

## Verification

Marketing (Phase A):
```bash
pnpm install
pnpm --filter sow-marketing build
ls delivery/marketing/out            # index.html, about/, docs/, zh-Hant/, robots.txt, sitemap.xml
grep -o 'id="airplay"' delivery/marketing/out/docs/index.html
grep -o 'youtube-nocookie.com/embed/4X4RQxU7SlU' delivery/marketing/out/index.html
grep -o 'app.streamofworship.com/register' delivery/marketing/out/index.html
grep -o 'app.streamofworship.com/zh-Hant' delivery/marketing/out/zh-Hant/index.html   # zh CTA
```
Visual: `pnpm --filter sow-marketing dev` → open `http://localhost:3001/` and `/zh-Hant/` in a browser; confirm hero, video embed, toggle navigates between locales, `/docs` has the AirPlay anchor.

Webapp (Phase B):
```bash
pnpm --filter sow-webapp test         # full suite green
pnpm --filter sow-webapp typecheck && pnpm --filter sow-webapp lint
pnpm --filter sow-webapp dev          # then:
curl -sI http://localhost:8080/       # unauthenticated → 307 Location: /login?callbackUrl=/
curl -sI http://localhost:8080/about  # → 307 (no longer public)
curl -s http://localhost:8080/robots.txt   # contains "Disallow: /", no sitemap line
```
Grep proof of clean cutover: `grep -rn "PublicLanding\|home.signedOut\|BuildStamp" delivery/webapp/src/` → no hits (except none); `grep -rn '"/about"\|"/docs"' delivery/webapp/src --include=*.tsx | grep -v test` → no internal-path hits.

## Assumptions & contingencies

- Domains `streamofworship.com` / `app.streamofworship.com` as decided; DNS and the two Vercel projects are wired outside this repo. Default fallbacks in code use these; env vars (`NEXT_PUBLIC_APP_URL`, `NEXT_PUBLIC_MARKETING_URL`, `NEXT_PUBLIC_SITE_URL`) override.
- If `HomePageClient.tsx` turns out to live outside `src/app/page/`, only `PublicLanding.tsx` + `BuildStamp.tsx` are deleted and step B2's move is skipped.
- If static export + `next/font` (Geist) fails under `output: "export"`, drop web fonts from the marketing layout and use the CSS font stack — cosmetic only.
- YouTube video `4X4RQxU7SlU` is reused as the demo; new videos are added later by editing the landing page's videos section.
