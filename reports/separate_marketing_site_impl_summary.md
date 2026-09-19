# Implementation Summary: Separate Marketing Site from Webapp

**Issue:** [#213](https://github.com/mhuang74/stream_of_worship/issues/213) · **Spec:** `specs/separate-marketing-site.md`
**Branch:** `separate_public_pages_from_app` · **Commit:** `bcbd76f8` (pushed, up to date with origin)
**Date:** 2026-09-19 · **Status:** COMPLETE — Phase A + Phase B, all verification green

## What was built

The public marketing surface (landing `/`, `/about`, `/docs`) moved out of `delivery/webapp` into a new independently-deployable static Next.js site at `delivery/marketing`. The webapp is now login-first: unauthenticated visitors to any app path get redirected to `/login`. Marketing owns `https://streamofworship.com` (root); the webapp moves to `https://app.streamofworship.com`. Bilingual: English at root paths, 繁體中文 under `/zh-Hant/…`, language toggle via plain links (no auto-detect).

## Phase A — `delivery/marketing` (new pnpm workspace)

### Structure

```
delivery/marketing/
├── package.json              # name sow-marketing, next@16.2.6, react@19.2.4, tailwindcss@^4
├── next.config.ts            # output: "export", images unoptimized
├── tsconfig.json             # paths @/* -> ./src/*
├── postcss.config.mjs, eslint.config.mjs, .gitignore
├── public/robots.txt         # Allow all + Sitemap: https://streamofworship.com/sitemap.xml
└── src/
    ├── messages.ts           # typed two-locale dictionary (MESSAGES, Locale, t(locale, key))
    ├── lib/urls.ts           # SITE_URL / APP_URL with env fallbacks
    ├── lib/utils.ts          # cn() (clsx + tailwind-merge) — 3+ call sites, shadcn seam
    ├── components/
    │   ├── ui/button.tsx     # local cva buttonVariants (default + outline; no @base-ui dep)
    │   ├── SiteHeader.tsx    # server component; {locale, path} props; locale toggle link
    │   ├── SiteFooter.tsx    # brand + © only (no BuildStamp)
    │   └── LandingPage.tsx / AboutPage.tsx / DocsPage.tsx   # locale-parameterized
    └── app/
        ├── layout.tsx        # <html lang="en">, metadata, apple-icon
        ├── page.tsx / about/page.tsx / docs/page.tsx
        ├── sitemap.ts        # 6 URLs; export const dynamic = "force-static" (required by static export)
        ├── globals.css       # copied verbatim (Tailwind v4 + shadcn tokens + .gradient-hero)
        └── zh-Hant/          # layout wraps children in <div lang="zh-Hant">; mirrors 3 pages
```

### Key decisions

- **Shared locale-parameterized page components** with thin per-route files instead of duplicating JSX across en/zh-Hant trees.
- **`SiteHeader` takes `path` prop** (not `usePathname`) so it stays a server component; toggle href swaps the `/zh-Hant` prefix.
- **Local minimal `buttonVariants`** (cva) instead of porting the full shadcn Button — avoids the `@base-ui/react` dependency; only the `cn(buttonVariants(...))` call pattern was copied.
- **`next/font`/Geist omitted** — plain `antialiased` class instead; avoids build-time Google Fonts fetch risk (spec contingency: cosmetic-only).
- **Deps mirror webapp versions**; `tw-animate-css` and `shadcn` added because the verbatim `globals.css` imports them.

### Content parity

- Landing: hero, CSS TV mockup, features grid, demo iframe (`youtube-nocookie.com/embed/4X4RQxU7SlU`), 4-step how-it-works, bottom CTA. CTAs → `${APP_URL}/register` and `${APP_URL}/login`.
- Docs: keeps `id="airplay" scroll-mt-16` — in-app ControllerPlayer links to `/docs#airplay` on the marketing domain now.
- Messages: all spec'd `home.signedOut.*`, `about.*`, `docs.*` keys copied verbatim for both locales, plus `nav.home`/`nav.docs`/`nav.main.ariaLabel`. Unused webapp keys (`home.signedOut.title/subtitle`, `nav.features/howItWorks/songs`) were omitted.

### Build + visual proof

- `pnpm --filter sow-marketing build` → `out/` with `index.html`, `about.html`, `docs.html`, `zh-Hant.html`, `zh-Hant/about.html`, `zh-Hant/docs.html`, `robots.txt`, `sitemap.xml` (6 `<url>`), favicon/apple-icon.
- HTML grep checks: `id="airplay"` ✓, YouTube embed ✓, `app.streamofworship.com/register` in both locales ✓, `lang="zh-Hant"` ✓, locale-relative hrefs ✓.
- Headless-Chrome screenshots of `/` and `/zh-Hant/` (dev server on 3001): hero, gradient text, TV mockup, nav, 繁體中文/English toggle render correctly. Toggle: zh → `/`, en → `/zh-Hant`. Note: `/zh-Hant/` 308-redirects to `/zh-Hant` (trailing-slash normalization) — use `curl -sL`.
- Marketing `typecheck` + `lint`: clean.

## Phase B — webapp becomes login-first

### Page/route changes

| Change | Detail |
|---|---|
| `src/app/page.tsx` | Signed-out → `redirect("/login")` (next/navigation). Signed-in path unchanged (same dashboard queries + props, `updatedAt.toISOString()`). |
| `HomePageClient.tsx` | Moved `src/app/page/` → `src/app/` via `git mv`. |
| Deleted | `page/PublicLanding.tsx`, `page/BuildStamp.tsx`, `app/about/`, `app/docs/`, `app/sitemap.ts`, `lib/siteUrl.ts`, `lib/i18n/messages/docs.ts`. |
| `src/proxy.ts` | `PUBLIC_PATHS` drops `/`, `/about`, `/docs` (keeps `/login`, `/register`, password-reset, `/api/auth`, `/api/health`, `/share`, `/api/share`, sw files). |
| i18n | All `home.signedOut.*` + `about.*` keys removed from both locales in `messages/core.ts`; kept `nav.about`, `brand.name`. `docsBundle` import + merge entry removed from `messages.ts`. |
| `src/app/robots.ts` | Rewritten: `rules: { userAgent: "*", disallow: "/" }`, no sitemap — app subdomain disallows all crawlers. |

### New helper + link cutovers

- `src/lib/marketing-url.ts`: `getMarketingUrl(locale)` → `process.env.NEXT_PUBLIC_MARKETING_URL ?? "https://streamofworship.com"`, appends `/zh-Hant` for zh-Hant locale.
- `Header.tsx` + `BottomNav.tsx`: signed-out About link → `` `${getMarketingUrl(locale)}/about` `` (both destructure `locale` from `useLocale()`).
- `ControllerPlayer.tsx` line ~1290: iPhone AirPlay fallback → `` `${getMarketingUrl(locale)}/docs#airplay` ``, `target="_blank" rel="noreferrer"` kept.
- `NEXT_PUBLIC_MARKETING_URL=https://streamofworship.com` documented in `.env.example` and `.env.production.example`.

### Test updates

- Deleted `src/test/app/home/PublicLanding.test.tsx`.
- `src/test/app/pages.test.tsx`: DocsPage describe removed; HomePage test asserts redirect — the `redirect` mock **throws** `NEXT_REDIRECT:/login` like real Next (a non-throwing mock lets the page fall through to the signed-in branch); `HomePageClient.test.tsx` import path updated.
- `src/test/proxy.test.ts`: locale-cookie tests repointed from `/` to `/login` (same `withAutoLocaleCookie` behavior); new case — unauthenticated `/` → 307 to `/login`.
- Link-destination tests (`Header.test.tsx`, `BottomNav.test.tsx`, `header-avatar.test.tsx`, `ControllerPlayer.test.tsx`): assert `https://streamofworship.com[/zh-Hant]/…` URLs (default, no env set).
- i18n `messages.test.ts` en/zh key-parity test survived key removal unchanged.

## Verification

| Check | Result |
|---|---|
| `pnpm --filter sow-marketing build` | ✅ static export, all 6 pages + SEO files |
| Marketing typecheck + lint | ✅ clean |
| `pnpm --filter sow-webapp test` | ✅ **2452 passed, 3 skipped** (138 files) |
| Webapp typecheck | ✅ clean |
| Webapp lint | ✅ 0 errors; 7 warnings byte-identical to pre-change HEAD (verified via `git stash`) |
| Live dev server `GET /` | ✅ `307 → /login?callbackUrl=%2F` |
| `GET /about` | ✅ `307 → /login?callbackUrl=%2Fabout` |
| `/robots.txt` | ✅ `User-Agent: *` / `Disallow: /` only |
| `/sitemap.xml` | ✅ 404 (route deleted) |
| `/api/health` | ✅ 204 |
| Grep proofs | ✅ no `PublicLanding`/`home.signedOut`/`BuildStamp`/`siteUrl`/internal `"/about"`·`"/docs"` hrefs in non-test src |
| `graphify update .` | ✅ 10154 nodes, 20593 edges |

## Deployment notes (not yet done — infra work)

1. Provision `app.streamofworship.com` → webapp; point `streamofworship.com` → marketing static export (`delivery/marketing/out/`).
2. Set `NEXT_PUBLIC_MARKETING_URL` in webapp prod env (fallback default already correct).
3. Set `NEXT_PUBLIC_APP_URL` on marketing to the app domain (fallback default already `https://app.streamofworship.com`).
4. Webapp dev server: `pnpm dev` on 8080; marketing dev: `next dev -p 3001`.
