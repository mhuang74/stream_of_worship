# Webapp Home Dashboard & Sign-out v4 (i18n Auto-Detection via Proxy)

> **Supersedes v3.** v3's Phase A called `cookies().set()` inside
> `resolveUserLocale()`, which runs in Server Components (`layout.tsx`,
> `page.tsx`). Next.js App Router Server Components **cannot** set cookies —
> `cookies().set()` only works in Server Actions, Route Handlers, or the
> request-time middleware (`proxy.ts` in Next.js 16). v4 moves
> `Accept-Language` parsing and cookie **persistence** to `src/proxy.ts`
> (the Next.js 16 middleware convention, already present in this repo) and
> makes `resolveUserLocale()` **read-only** (it never writes a cookie). The
> first-visit render is still correct because `resolveUserLocale()` falls
> back to `Accept-Language` when the cookie is absent. This is a complete,
> standalone restatement of the plan; v3 is not needed alongside it.

## Summary

This spec builds on v2/v3, adding **automatic browser-locale detection** so
first-time visitors see Traditional Chinese or English based on their
`Accept-Language` header, and **expanding i18n coverage** so every new string
on the dashboard, public landing page, avatar dropdown, settings sign-out
section, and error surfaces is fully translated.

Locale resolution has two independent layers:

**Persistence (request-time, in `src/proxy.ts`):** On every matched request,
if the `sow_locale` cookie is absent **and** the Better Auth session cookie
is absent (a truly unauthenticated first visit), parse `Accept-Language` and
set `sow_locale` on the `NextResponse`. No auth or DB call — only cheap
cookie reads. Authenticated users with no `sow_locale` cookie are skipped:
the DB setting (`user_settings.locale`) is authoritative and the existing
settings PUT syncs the cookie.

**Render-time (read-only, in `resolveUserLocale()`):** Reads
`user_settings.locale` (authed) → `sow_locale` cookie → `Accept-Language`
header → hard `en`. It **never** calls `cookies().set()`. On the very first
visit the proxy sets the cookie on the *response*, but Server Components
read cookies from the *request*, so the cookie is absent for that first
render — `resolveUserLocale()` falls back to `Accept-Language` so the first
render is still correct; subsequent requests see the persisted cookie.

Resolution order (highest to lowest priority) at render time:
1. Authenticated user's saved account setting (`user_settings.locale`)
2. `sow_locale` cookie (explicit user choice or prior auto-detection by the proxy)
3. `Accept-Language` header → best match (`zh-Hant` if any `zh-*`/`zh` tag is present; otherwise `en`)
4. Hard fallback to `en`

A shared `parseAcceptLanguage()` helper is used by both the proxy and
`resolveUserLocale()` so detection logic is defined once.

All new UI strings added in v2 (dashboard greeting, stat cards, section
titles, empty states, CTAs, landing page hero/features/how-it-works,
sign-out labels, avatar dropdown, toasts) are present in both `en` and
`zh-Hant` with full parity enforced by the `bundle()` type system.

---

## User Decisions (added/changed from v2; v3 cookie-writing behavior reverted)

| Decision | Choice |
|----------|--------|
| Locale auto-detection — persistence | `src/proxy.ts` sets `sow_locale` from `Accept-Language` on the `NextResponse` for unauthenticated first visits only (no session cookie, no `sow_locale` cookie). No auth/DB call in the proxy. |
| Locale auto-detection — render | `resolveUserLocale()` is **read-only**; it parses `Accept-Language` as a fallback when the cookie is absent and **never** calls `cookies().set()`. |
| Authenticated user, no `sow_locale` cookie | Proxy skips cookie-setting. DB `user_settings.locale` is authoritative at render; the existing settings PUT syncs the cookie on next save. |
| Auto-detect persistence | Yes — detected locale written to `sow_locale` cookie (365d, path=/, samesite=lax, secure in production) by the proxy. |
| Authenticated fallback | If no saved `user_settings.locale`, fall back to cookie / `Accept-Language` / `en` (same as unauthenticated). |
| zh-Hant matching rule | Any `zh*` language tag (`zh`, `zh-TW`, `zh-HK`, `zh-CN`, `zh-SG`, `zh-Hant`, `zh-Hans`) maps to `zh-Hant`. |
| Non-zh non-en tags | Fall back to `en`. |
| Cookie update on explicit change | Settings language picker PUT also rewrites `sow_locale` cookie (existing behavior; no change). |
| i18n key parity | Every new key present in both `en` and `zh-Hant` blocks; `bundle()` enforces compile-time parity. |
| Interpolation pattern | `${name}`, `${n}`, `${percent}` replaced with `.replace()` at call site (same as v2). |
| Error toast i18n | "Sign out failed" added as a keyed message (`settings.signOut.error`) instead of a hardcoded English string. |

---

## Current State (relevant to i18n changes)

| Concern | Location | Status |
|---|---|---|
| Locale resolver | `src/lib/i18n/server.ts:18` | Reads `sow_locale` cookie → authenticated DB setting → `en` fallback. **No `Accept-Language` parsing; never writes a cookie (correct).** v4 adds a read-only `Accept-Language` fallback. |
| Request-time proxy | `src/proxy.ts` | Next.js 16 middleware convention (renamed from `middleware.ts`). Runs before every matched request. Currently only does auth gating. v4 adds `sow_locale` persistence here. |
| Cookie write (settings) | `src/app/api/settings/route.ts:261` | Already sets `sow_locale` cookie on settings save (Route Handler — valid). Reuse the same cookie policy in the proxy. |
| i18n messages | `src/lib/i18n/messages/core.ts` | Has `home.title`, `home.subtitle`, `home.viewSongsets`. Needs new keys for dashboard + landing + sign-out. |
| Bundle type safety | `src/lib/i18n/messages.ts:30` | `bundle()` enforces identical keys across `en` and `zh-Hant`. Missing key = compile error. |
| `t()` lookup | `src/lib/i18n/messages.ts:86` | Pure function `t(locale, key)` used by client components via `useLocale()` hook. |
| Public landing locale | `src/app/page.tsx:8` | Already calls `resolveUserLocale()` — after this change it gets auto-detected locale for free. |
| Auth config | `src/lib/auth.ts:38` | `advanced.useSecureCookies = process.env.NODE_ENV === "production"`. Session cookie is `better-auth.session_token` (dev) / `__Secure-better-auth.session_token` (prod). |
| Next.js version | `package.json` | 16.2.6 — `proxy.ts` is the middleware convention; `middleware.ts` is deprecated. |

---

## Implementation Plan

### Phase A: Locale Auto-Detection — proxy + read-only resolver

**Goal:** Detect `Accept-Language` and persist `sow_locale` at request time
(in `src/proxy.ts`, where `NextResponse.set` is valid), and expose the
detected locale to Server Components read-only via `resolveUserLocale()`.

#### A1. Add a shared `Accept-Language` parser

**File:** `src/lib/i18n/accept-language.ts` **(new)**

A pure, framework-agnostic helper used by both the proxy and the resolver so
detection logic lives in one place.

```ts
import { Locale } from "./messages";

/**
 * Parse the Accept-Language header and return the best matching locale.
 * - Any `zh*` tag → "zh-Hant"
 * - "en" or no match → "en"
 * - Ignores quality values; first match wins.
 *
 * Framework-agnostic and pure so it can be imported by the request-time
 * proxy (src/proxy.ts) and the Server Component resolver
 * (src/lib/i18n/server.ts) without either depending on the other.
 */
export function parseAcceptLanguage(headerValue: string | null): Locale {
  if (!headerValue) return "en";
  const tags = headerValue
    .split(",")
    .map((s) => s.split(";")[0].trim().toLowerCase());
  for (const tag of tags) {
    if (tag.startsWith("zh")) return "zh-Hant";
    if (tag === "en") return "en";
  }
  return "en";
}
```

#### A2. Persist `sow_locale` in the proxy

**File:** `src/proxy.ts`

Add the locale cookie to the `NextResponse` for unauthenticated first
visits, **before** any auth gating. This is the only place that writes the
auto-detected cookie; it runs on the request (edge/server) where
`NextResponse.set` is valid. No auth or DB call — only cheap cookie reads.

```ts
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { isLocale } from "@/lib/i18n/messages";
import { parseAcceptLanguage } from "@/lib/i18n/accept-language";

const PUBLIC_PATHS = ["/", "/login", "/register", "/api/auth", "/share", "/api/share"];

function isPublicPath(pathname: string) {
  if (pathname.endsWith("/play/projection")) return true;
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

const LOCALE_COOKIE = "sow_locale";

/**
 * Better Auth session cookie names. `useSecureCookies` is gated on
 * NODE_ENV === "production", so the proxy must recognize both forms.
 */
const SESSION_COOKIE_NAMES = [
  "better-auth.session_token",
  "__Secure-better-auth.session_token",
];

function hasSessionCookie(req: NextRequest): boolean {
  return SESSION_COOKIE_NAMES.some((name) => req.cookies.get(name) != null);
}

/**
 * If this is a truly unauthenticated first visit (no sow_locale cookie AND
 * no session cookie), persist the Accept-Language-detected locale so
 * subsequent visits stay consistent. Runs before auth gating; no auth/DB
 * call — only cheap cookie reads. Authenticated users with no sow_locale
 * cookie are skipped: the DB locale is authoritative and the settings PUT
 * syncs the cookie.
 */
function withAutoLocaleCookie(req: NextRequest, res: NextResponse): NextResponse {
  const existing = req.cookies.get(LOCALE_COOKIE)?.value;
  if (existing && isLocale(existing)) return res;
  if (hasSessionCookie(req)) return res;
  const detected = parseAcceptLanguage(req.headers.get("accept-language"));
  res.cookies.set(LOCALE_COOKIE, detected, {
    path: "/",
    maxAge: 60 * 60 * 24 * 365, // 365 days
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });
  return res;
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isPublicPath(pathname)) {
    return withAutoLocaleCookie(request, NextResponse.next());
  }

  const session = await auth.api.getSession({ headers: request.headers });

  if (!session) {
    if (pathname.startsWith("/api/")) {
      return withAutoLocaleCookie(
        request,
        NextResponse.json({ error: "Unauthorized" }, { status: 401 })
      );
    }
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("callbackUrl", pathname);
    return withAutoLocaleCookie(request, NextResponse.redirect(loginUrl));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
```

**Rationale:**
- The proxy is the Next.js 16 middleware; `NextResponse.cookies.set` is valid
  here. This is the only place the auto-detected cookie is written.
- The cookie is set **only** when there is no valid `sow_locale` cookie **and**
  no session cookie — i.e. a truly unauthenticated first visit. This avoids an
  unnecessary `Set-Cookie` on every request and keeps the proxy free of auth
  / DB calls (it only reads cookies and one header).
- Authenticated users with no `sow_locale` cookie are skipped: the DB setting
  is authoritative at render and the existing settings PUT syncs the cookie
  on next save. The proxy never touches their locale cookie.
- `withAutoLocaleCookie` wraps every `NextResponse` branch (public, API 401,
  redirect) so the first-visit cookie is set regardless of the public path
  shape.
- The `secure` flag is environment-gated so local dev (http://localhost) does
  not drop the cookie.

#### A3. Make `resolveUserLocale()` read-only with an `Accept-Language` fallback

**File:** `src/lib/i18n/server.ts`

`resolveUserLocale()` runs in Server Components (`layout.tsx`, `page.tsx`)
where `cookies().set()` is invalid. It becomes **read-only**: it parses
`Accept-Language` as a fallback when the cookie is absent and **never** calls
`cookies().set()`.

```ts
import { cookies, headers } from "next/headers";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userSettings } from "@/db/schema";
import { eq } from "drizzle-orm";
import { isLocale, Locale } from "./messages";
import { parseAcceptLanguage } from "./accept-language";

/**
 * Resolve the display language for server-rendered markup (the `<html lang>`
 * attribute and the initial locale passed to the client LocaleProvider).
 *
 * Read-only: this function NEVER writes a cookie. Cookie persistence for the
 * Accept-Language-detected locale happens in `src/proxy.ts` (the Next.js 16
 * middleware), where `NextResponse.cookies.set` is valid. On the very first
 * visit the proxy sets the cookie on the response, but Server Components
 * read cookies from the request, so the cookie is absent for that first
 * render — this function falls back to `Accept-Language` so the first render
 * is still correct; subsequent requests see the persisted cookie.
 *
 * Priority: user_settings.locale (authed) → sow_locale cookie →
 * Accept-Language header → `en`.
 */
export async function resolveUserLocale(): Promise<Locale> {
  const cookieLocale = (await cookies()).get("sow_locale")?.value;
  const headerValue = (await headers()).get("accept-language");
  const detected = parseAcceptLanguage(headerValue);

  const fallback = (): Locale => (isLocale(cookieLocale) ? cookieLocale : detected);

  try {
    const session = await auth.api.getSession({ headers: await headers() });
    if (!session?.user) return fallback(); // public pages: cookie/header drives locale

    const userId = Number(session.user.id);
    const rows = await db
      .select({ locale: userSettings.locale })
      .from(userSettings)
      .where(eq(userSettings.userId, userId));

    const value = rows[0]?.locale;
    return isLocale(value) ? value : fallback(); // authenticated: account setting wins
  } catch {
    return fallback();
  }
}
```

**Rationale:**
- `parseAcceptLanguage` is the same helper the proxy uses, so detection is
  defined once. `resolveUserLocale()` imports it; the proxy imports it too —
  neither depends on the other.
- `resolveUserLocale()` never calls `cookies().set()`. It is safe to call from
  any Server Component (`layout.tsx`, `page.tsx`).
- On the first visit, the cookie is absent for the request, so this returns
  `detected` — the same value the proxy is persisting on the response — so
  the first render matches what subsequent visits will see. No flash, no
  mismatch.
- For authenticated users with no saved setting, `detected` is returned
  immediately (same as public). When they later save Settings, the PUT writes
  `user_settings.locale` and the cookie (existing behavior).

---

### Phase B: i18n Messages — expand `src/lib/i18n/messages/core.ts`

Add the following keys to **both** the `en` and `zh-Hant` objects. The
`bundle()` type system will enforce parity at compile time.

#### B1. Dashboard (signed-in home)

```
home.welcomeBack                       "Welcome back, ${name}"                  "${name}，歡迎回來"
home.stats.songsetsCreated             "Songsets created"                       "已建立詩歌集"
home.stats.songsetsRendered            "Songsets rendered"                      "已渲染詩歌集"
home.stats.songsetsShared              "Songsets shared"                        "已分享詩歌集"
home.stats.favoriteSongs               "Favorite songs"                         "最愛詩歌"
home.stats.catalogSongs                "Songs in catalog"                       "詩歌目錄總數"
home.section.recentSongsets            "Recent songsets"                        "最近的詩歌集"
home.section.recentFavorites           "Favorite songs"                         "我的最愛"
home.section.communityFavorites        "From the community"                     "來自社群的最愛"
home.action.viewAll                    "View all"                               "檢視全部"
home.badge.favoritedBy                 "Favorited by ${n}"                      "${n} 人最愛"
home.empty.recentSongsets              "No songsets yet"                        "尚無詩歌集"
home.empty.recentFavorites             "No favorites yet"                       "尚無最愛"
home.empty.communityFavorites          "No community favorites"                 "尚無社群最愛"
home.empty.createSongset               "Create your first songset"              "建立您的第一個詩歌集"
home.empty.browseCatalog               "Browse the catalog"                     "瀏覽曲目"
```

#### B2. Public landing page (signed-out home)

```
home.signedOut.title                   "Stream of Worship"                      "Stream of Worship"
home.signedOut.subtitle                "Worship music transition and playback system. Manage songsets, render audio and video, and lead worship seamlessly."  "敬拜音樂轉場與播放系統。管理詩歌集、渲染音訊與影片，無縫帶領敬拜。"
home.signedOut.heroTag                 "Seamless worship music transitions"     "無縫敬拜音樂轉場"
home.signedOut.heroTitle               "Lead worship without awkward pauses."   "帶領敬拜，無需尷尬停頓。"
home.signedOut.heroDescription         "Stream of Worship analyzes tempo, key, and structure to generate smooth transitions between songs — then renders audio and lyrics videos for your service."  "Stream of Worship 分析速度、調性與結構，生成歌曲間的流暢轉場——並為您的服事渲染音訊與歌詞影片。"
home.signedOut.ctaPrimary              "Get started free"                       "免費開始使用"
home.signedOut.ctaSecondary            "Sign in"                                "登入"
home.signedOut.ctaFooter               "No credit card required · Free for personal use"  "無需信用卡 · 個人使用免費"
home.signedOut.featuresTitle           "Everything you need for a seamless service"  "無縫服事所需的一切"
home.signedOut.featuresDescription     "From song selection to rendered video, Stream of Worship handles the technical details so you can focus on leading."  "從選歌到渲染影片，Stream of Worship 處理技術細節，讓您專注帶領。"
home.signedOut.feature.build           "Build songsets"                         "建立詩歌集"
home.signedOut.feature.buildDesc       "Curate songs from a catalog of 300+ worship songs. Reorder, adjust keys, and preview transitions before you commit."  "從 300+ 首敬拜詩歌曲目中精選。重新排序、調整調性、預覽轉場。"
home.signedOut.feature.render          "Render audio & video"                   "渲染音訊與影片"
home.signedOut.feature.renderDesc      "Generate a single audio file with smooth crossfades, plus a lyrics video with your choice of template, resolution, and fonts."  "生成帶有流暢交叉淡入淡出的單一音訊檔案，以及可選擇模板、解析度與字體的歌詞影片。"
home.signedOut.feature.share           "Share with your team"                   "與團隊分享"
home.signedOut.feature.shareDesc       "Share rendered songsets with your worship team via a link. No accounts needed for viewers."  "透過連結分享渲染後的詩歌集給敬拜團隊。觀看者無需帳號。"
home.signedOut.howItWorksTitle         "How it works"                           "運作方式"
home.signedOut.step1                   "Pick your songs"                        "選擇詩歌"
home.signedOut.step1Desc               "Browse the catalog and add songs to a songset."  "瀏覽曲目並加入詩歌集。"
home.signedOut.step2                   "Tune transitions"                       "調整轉場"
home.signedOut.step2Desc               "Adjust gap beats, key shifts, and preview each transition."  "調整間隔拍數、調性變化，並預覽每個轉場。"
home.signedOut.step3                   "Render"                                 "渲染"
home.signedOut.step3Desc               "Generate the audio mix and lyrics video in the cloud."  "在雲端生成音訊混音與歌詞影片。"
home.signedOut.step4                   "Lead & share"                           "帶領與分享"
home.signedOut.step4Desc               "Play from any device or share a link with your team."  "在任何裝置播放，或分享連結給團隊。"
home.signedOut.ctaBottomTitle          "Ready to lead worship seamlessly?"      "準備好無縫帶領敬拜了嗎？"
home.signedOut.ctaBottomDesc           "Join the community of worship leaders using Stream of Worship."  "加入使用 Stream of Worship 的敬拜帶領者社群。"
home.signedOut.ctaBottomPrimary         "Create your free account"               "建立免費帳號"
home.signedOut.nav.features            "Features"                               "功能"
home.signedOut.nav.howItWorks          "How it works"                           "運作方式"
home.signedOut.nav.songs               "Songs"                                  "詩歌"
```

#### B3. Settings & sign-out

```
settings.section.account               "Account"                                "帳號"
settings.signOut                        "Sign out"                               "登出"
settings.signOut.success               "Signed out"                             "已登出"
settings.signOut.error                 "Sign out failed"                        "登出失敗"
```

#### B4. Header avatar dropdown

```
nav.signOut                            "Sign out"                               "登出"
```

**Note on `nav.settings`:** Already exists in `core.ts` as `"Settings"` / `"設定"`. Reuse it.

**Note on auth buttons:** Reuse existing keys `auth.signIn.submit` ("Sign in" / "登入") and `auth.register.submit` ("Create account" / "建立帳號") from the auth section.

**Note on error toast in `handleSignOut`:** Replace the hardcoded `"Sign out failed"` with `t("settings.signOut.error")` in both Header and Settings page handlers.

---

### Phase C: Proxy — make `/` public (unchanged from v2/v3)

**File:** `src/proxy.ts:4` *(now folded into the Phase A rewrite of `proxy.ts`)*

Add `"/"` to `PUBLIC_PATHS`:

```ts
const PUBLIC_PATHS = ["/", "/login", "/register", "/api/auth", "/share", "/api/share"];
```

Rationale: The root page now serves both authenticated (dashboard) and unauthenticated (landing) content. `resolveUserLocale()` handles locale for both cases. *(This is already included in the Phase A `proxy.ts` listing above.)*

---

### Phase D: Header — avatar dropdown with i18n (updated from v2)

**File:** `src/components/layout/Header.tsx`

#### D1. Use `t()` for all new strings

```tsx
<DropdownMenuItem disabled className="text-xs text-muted-foreground">
  <User className="size-4 mr-2" />
  {user.name}
</DropdownMenuItem>
<DropdownMenuSeparator />
<DropdownMenuItem onClick={() => router.push("/settings")}>
  <Settings className="size-4 mr-2" />
  {t("nav.settings")}  {/* already exists */}
</DropdownMenuItem>
<DropdownMenuItem onClick={handleSignOut}>
  <LogOut className="size-4 mr-2" />
  {t("nav.signOut")}
</DropdownMenuItem>
```

#### D2. Sign-out handler with i18n error toast

```ts
async function handleSignOut() {
  try {
    await signOut();
    toast.success(t("settings.signOut.success"));
    router.push("/login");
    router.refresh();
  } catch {
    toast.error(t("settings.signOut.error"));  // was hardcoded "Sign out failed"
  }
}
```

#### D3. Conditional nav links (signed-out landing)

```tsx
{user ? (
  <>
    <Link href="/songsets">{t("nav.songsets")}</Link>
    <Link href="/favorites">{t("nav.favorites")}</Link>
    <Link href="/settings">{t("nav.settings")}</Link>
  </>
) : (
  <>
    <a href="#features">{t("home.signedOut.nav.features")}</a>
    <a href="#how-it-works">{t("home.signedOut.nav.howItWorks")}</a>
    <Link href="/songs">{t("home.signedOut.nav.songs")}</Link>
  </>
)}
```

#### D4. Signed-out auth buttons

```tsx
<Link href="/login" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
  {t("auth.signIn.submit")}
</Link>
<Link href="/register" className={cn(buttonVariants({ size: "sm" }))}>
  {t("auth.register.submit")}
</Link>
```

---

### Phase E: Settings page sign-out — i18n (updated from v2)

**File:** `src/app/settings/page.tsx`

#### E1. "Account" section with keyed strings

```tsx
<div className="mt-8 border-t pt-6">
  <h2 className="text-lg font-semibold mb-3">{t("settings.section.account")}</h2>
  <Button variant="outline" onClick={handleSignOut} disabled={isSigningOut}>
    {isSigningOut ? (
      <Loader2 className="size-4 mr-2 animate-spin" />
    ) : (
      <LogOut className="size-4 mr-2" />
    )}
    {t("settings.signOut")}
  </Button>
</div>
```

#### E2. Handler with keyed error toast

```ts
async function handleSignOut() {
  setIsSigningOut(true);
  try {
    await signOut();
    toast.success(t("settings.signOut.success"));
    router.push("/login");
    router.refresh();
  } catch {
    toast.error(t("settings.signOut.error"));  // keyed, not hardcoded
  } finally {
    setIsSigningOut(false);
  }
}
```

---

### Phase F: Home page — branch on session (unchanged from v2)

**File:** `src/app/page.tsx`

```ts
export default async function HomePage() {
  const locale = await resolveUserLocale();  // read-only; includes Accept-Language fallback
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    return <PublicLanding locale={locale} />;
  }
  // ... dashboard path
}
```

No code change needed here — `resolveUserLocale()` now handles detection internally (read-only). The `locale` prop is passed to both `PublicLanding` and `HomePageClient` so they can call `t(locale, key)`.

---

### Phase G: PublicLanding — use `t(locale, key)` for all strings

**File:** `src/app/page/PublicLanding.tsx`

Receive `locale: Locale` as a prop and call `t(locale, "home.signedOut.*")` for every displayed string. No hardcoded English remains.

Examples:
- Hero tagline: `t(locale, "home.signedOut.heroTag")`
- Hero title: `t(locale, "home.signedOut.heroTitle")`
- Feature card titles/descriptions: `t(locale, "home.signedOut.feature.build")`, `t(locale, "home.signedOut.feature.buildDesc")`, etc.
- Step titles/descriptions: `t(locale, "home.signedOut.step1")`, `t(locale, "home.signedOut.step1Desc")`, etc.
- CTA buttons: `t(locale, "home.signedOut.ctaPrimary")`, `t(locale, "auth.signIn.submit")`
- Footer / nav: `t(locale, "home.signedOut.nav.features")`, etc.

---

### Phase H: HomePageClient — use `t(locale, key)` for all strings

**File:** `src/app/page/HomePageClient.tsx`

Receive `locale: Locale` as an additional prop. Use `t(locale, "home.*")` for:
- Greeting: `t(locale, "home.welcomeBack").replace("${name}", userName)`
- Stat card labels: `t(locale, "home.stats.songsetsCreated")`, etc.
- Section headings: `t(locale, "home.section.recentSongsets")`, etc.
- "View all" links: `t(locale, "home.action.viewAll")`
- "Favorited by N" badge: `t(locale, "home.badge.favoritedBy").replace("${n}", String(favoriteCount))`
- Empty states + CTAs: `t(locale, "home.empty.recentSongsets")`, `t(locale, "home.empty.createSongset")`, etc.

Reuse existing keys for actions that already have them:
- Play: `songsets.action.play` (from `songsets.ts` bundle)
- Share: `songsets.action.share` (from `songsets.ts` bundle)

---

### Phase I: Tests — i18n + proxy assertions

#### I1. `src/test/lib/i18n/accept-language.test.ts` **(new)**

Unit-test the pure parser shared by the proxy and the resolver.

```ts
import { describe, it, expect } from "vitest";
import { parseAcceptLanguage } from "@/lib/i18n/accept-language";

describe("parseAcceptLanguage", () => {
  it("returns en for a null/empty header", () => {
    expect(parseAcceptLanguage(null)).toBe("en");
    expect(parseAcceptLanguage("")).toBe("en");
  });

  it("maps any zh* tag to zh-Hant (first match wins)", () => {
    expect(parseAcceptLanguage("zh-TW,en-US;q=0.9")).toBe("zh-Hant");
    expect(parseAcceptLanguage("zh,en;q=0.9")).toBe("zh-Hant");
    expect(parseAcceptLanguage("zh-CN")).toBe("zh-Hant");
    expect(parseAcceptLanguage("zh-Hant")).toBe("zh-Hant");
  });

  it("returns en for en or unknown tags", () => {
    expect(parseAcceptLanguage("en-US")).toBe("en");
    expect(parseAcceptLanguage("fr-FR,de;q=0.8")).toBe("en");
    expect(parseAcceptLanguage("ja,en;q=0.5")).toBe("en");
  });
});
```

#### I2. `src/test/lib/i18n/server.test.ts` — extend (read-only; no cookie write)

`resolveUserLocale()` is now read-only. Tests assert the `Accept-Language`
fallback influences the returned locale when the cookie is absent, and that
**`cookies().set` is never called** by `resolveUserLocale()`.

```ts
describe("resolveUserLocale Accept-Language fallback (read-only)", () => {
  it("detects zh-Hant from Accept-Language 'zh-TW' when no cookie and no session", async () => {
    mockCookie(undefined);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-TW,en-US;q=0.9" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("detects zh-Hant from Accept-Language 'zh' when no cookie and no session", async () => {
    mockCookie(undefined);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh,en;q=0.9" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("falls back to en for unknown language tags", async () => {
    mockCookie(undefined);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "fr-FR,de;q=0.8" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("en");
  });

  it("prefers cookie over Accept-Language", async () => {
    mockCookie("zh-Hant");
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "en-US" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("uses Accept-Language as the authenticated user's fallback when no DB locale and no cookie", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockCookie(undefined);
    mockSelectResult([{ locale: "fr" }]); // invalid DB value
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-HK" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("NEVER calls cookies().set() (read-only Server Component resolver)", async () => {
    const cookieStore = { get: () => undefined, set: vi.fn() } as any;
    vi.mocked(cookies).mockResolvedValue(cookieStore);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-TW" : null),
    } as any);
    await resolveUserLocale();
    expect(cookieStore.set).not.toHaveBeenCalled();
  });
});
```

> **Note:** the mock setup in this file (`mockCookie`, `mockSelectResult`,
> `vi.mocked(cookies).mockResolvedValue`) is the existing harness. The
> `mockCookie` helper already returns an object with a `get`; for the
> `set`-never-called test, override `cookies()` to return an object that
> also has a `set` spy so the assertion is meaningful. The existing tests
> that don't mock `headers().get("accept-language")` will get `undefined`
> → `parseAcceptLanguage` returns `en`, so their expected results are
> unchanged.

#### I3. `src/test/proxy.test.ts` **(new)** — proxy cookie persistence

Assert the proxy sets `sow_locale` from `Accept-Language` on the response for
unauthenticated first visits, and skips it when a session cookie or a valid
`sow_locale` cookie is present.

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { auth } from "@/lib/auth";
import { proxy } from "@/proxy";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

function req(url: string, opts: { cookie?: string; acceptLanguage?: string; sessionCookie?: boolean } = {}) {
  const headers = new Headers();
  if (opts.acceptLanguage) headers.set("accept-language", opts.acceptLanguage);
  const request = new NextRequest(new URL(url, "http://localhost:3000"), { headers });
  if (opts.cookie) request.cookies.set("sow_locale", opts.cookie);
  if (opts.sessionCookie) request.cookies.set("better-auth.session_token", "test");
  return request;
}

describe("proxy locale cookie", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sets sow_locale=zh-Hant from Accept-Language on a public first visit (no cookie, no session)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/"));
    expect(res.cookies.get("sow_locale")?.value).toBe("zh-Hant");
  });

  it("does not set sow_locale when a valid sow_locale cookie already exists", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/", { cookie: "zh-Hant", acceptLanguage: "en-US" }));
    expect(res.cookies.get("sow_locale")).toBeUndefined();
  });

  it("does not set sow_locale when a session cookie is present (authenticated user)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/songsets", { sessionCookie: true, acceptLanguage: "zh-TW" }));
    expect(res.cookies.get("sow_locale")).toBeUndefined();
  });

  it("sets sow_locale on the redirect response for an unauthenticated non-public path", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/songsets", { acceptLanguage: "zh-HK" }));
    expect(res.status).toBe(307);
    expect(res.cookies.get("sow_locale")?.value).toBe("zh-Hant");
  });

  it("defaults to en when Accept-Language is absent or unrecognized", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/"));
    expect(res.cookies.get("sow_locale")?.value).toBe("en");
  });
});
```

#### I4. `src/test/app/home/PublicLanding.test.tsx` — i18n coverage **(new)**

- Assert that `PublicLanding` renders `t(locale, "home.signedOut.heroTitle")` when `locale="zh-Hant"`.
- Assert that `PublicLanding` renders `t(locale, "home.signedOut.ctaPrimary")` in Traditional Chinese.

#### I5. `src/test/app/home/HomePageClient.test.tsx` — i18n coverage **(new)**

- Assert greeting uses `home.welcomeBack` with name interpolation.
- Assert stat card labels use `home.stats.*` keys.
- Assert "Favorited by N" badge uses `home.badge.favoritedBy` with `${n}` replaced.

#### I6. `src/test/app/settings-signout.test.tsx` — keyed error toast **(new)**

- Assert that sign-out failure shows `t("settings.signOut.error")` (not hardcoded English).

#### I7. `src/test/app/header-avatar.test.tsx` — keyed strings **(new)**

- Assert dropdown shows `t("nav.settings")` and `t("nav.signOut")`.
- Assert signed-out header shows `t("auth.signIn.submit")` and `t("auth.register.submit")`.

#### I8. `src/test/lib/i18n/messages.test.ts` — bundle parity

- Ensure all new keys appear in both `en` and `zh-Hant`. `bundle()` already enforces this at compile time; the test verifies runtime merging produces no blanks.

---

## Files Changed (Summary)

| File | Change |
|------|--------|
| `src/lib/i18n/accept-language.ts` | **New** — pure `parseAcceptLanguage()` shared by proxy and resolver |
| `src/lib/i18n/server.ts` | **Read-only** `resolveUserLocale()` with `Accept-Language` fallback; **never** calls `cookies().set()` |
| `src/proxy.ts` | Add `sow_locale` persistence from `Accept-Language` for unauthenticated first visits (response cookie); add `"/"` to `PUBLIC_PATHS` |
| `src/lib/i18n/messages/core.ts` | Add `home.*`, `settings.section.account`, `settings.signOut*`, `nav.signOut` keys (en + zh-Hant) |
| `src/components/layout/Header.tsx` | Avatar dropdown + conditional nav links; all strings via `t()`; error toast keyed |
| `src/app/page.tsx` | Branch on session; pass `locale` to `PublicLanding` and `HomePageClient` |
| `src/app/page/PublicLanding.tsx` | **New** — rich marketing landing page; all strings via `t(locale, key)` |
| `src/app/page/HomePageClient.tsx` | **New** — dashboard client component; all strings via `t(locale, key)` |
| `src/components/dashboard/DashboardSongsetCard.tsx` | **New** — lightweight songset card (strings reuse existing keys) |
| `src/components/dashboard/StatCard.tsx` | **New** — icon-enhanced stat card (labels from `home.stats.*`) |
| `src/components/songset/SongCard.tsx` | Add optional `favoriteCount?: number` prop → "Favorited by N" badge (label from `home.badge.favoritedBy`) |
| `src/app/settings/page.tsx` | Add "Account" section with Sign out button; all strings keyed; error toast keyed |
| `src/test/lib/i18n/accept-language.test.ts` | **New** — pure parser unit tests |
| `src/test/lib/i18n/server.test.ts` | Extend with `Accept-Language` fallback tests + `cookies().set`-never-called assertion (replaces v3 cookie-write tests) |
| `src/test/proxy.test.ts` | **New** — proxy `sow_locale` cookie persistence tests |
| `src/test/app/home/HomePageClient.test.tsx` | **New** — i18n assertions for dashboard strings |
| `src/test/app/home/PublicLanding.test.tsx` | **New** — i18n assertions for landing page strings |
| `src/test/app/settings-signout.test.tsx` | **New** — sign-out flow + keyed error toast |
| `src/test/app/header-avatar.test.tsx` | **New** — avatar dropdown + nav link i18n |
| `src/test/lib/db/dashboard.test.ts` | **New** — DB helper tests (unchanged from v2) |
| `src/test/accessibility/accessibility.test.tsx` | Extend with home/heading/label/keyboard assertions (unchanged from v2) |

## Out of Scope

- **Multi-locale beyond `en` and `zh-Hant`** — e.g., `zh-Hans`, `ja`, `ko` are out of scope.
- **Quality-value parsing in `Accept-Language`** — `q=0.9` is ignored; first tag wins. Acceptable for a two-locale app.
- **Per-device locale memory** — cookie is the only persistence mechanism.
- **Client-side `navigator.language` fallback** — server-side `Accept-Language` is sufficient for SSR; client rehydration receives the resolved locale from the server.
- **Proxy-side auth/DB locale lookup for authenticated users** — the proxy deliberately skips cookie-setting when a session cookie is present; the DB setting is authoritative at render and the settings PUT syncs the cookie. Adding a DB call to every proxied request would add latency for no benefit.
- **Android app locale sync** — Android app uses its own locale setting; no coupling to web cookie.
- **All other v2 out-of-scope items** remain unchanged (render_jobs counting, caching community favorites, real screenshots, separate pages, profile page, etc.).

## Verification Commands

```bash
pnpm --filter sow-webapp test
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build
```