# Webapp Home Dashboard & Sign-out v3 (with i18n Auto-Detection)

## Summary

This spec builds on v2, adding **automatic browser-locale detection** so first-time visitors see Traditional Chinese or English based on their `Accept-Language` header, and **expanding i18n coverage** so every new string on the dashboard, public landing page, avatar dropdown, settings sign-out section, and error surfaces is fully translated.

Locale resolution order (highest to lowest priority):
1. Authenticated user's saved account setting (`user_settings.locale`)
2. `sow_locale` cookie (explicit user choice or prior auto-detection)
3. `Accept-Language` header → best match (`zh-Hant` if any `zh-*` or `zh` tag is present; otherwise `en`)
4. Hard fallback to `en`

The detected locale is written to the `sow_locale` cookie (path=/, max-age=365 days) so subsequent visits (even after cookie expiry or on different devices) remain consistent. The cookie is updated whenever a user explicitly changes language in Settings.

All new UI strings added in v2 (dashboard greeting, stat cards, section titles, empty states, CTAs, landing page hero/features/how-it-works, sign-out labels, avatar dropdown, toasts) are present in both `en` and `zh-Hant` with full parity in the `bundle()` type system.

---

## User Decisions (added/changed from v2)

| Decision | Choice |
|----------|--------|
| Locale auto-detection | Server-side `Accept-Language` header parsing in `resolveUserLocale()` |
| Auto-detect persistence | Yes — detected locale written to `sow_locale` cookie (365d, path=/, secure in production, samesite=lax) |
| Authenticated fallback | If no saved `user_settings.locale`, fall back to cookie / Accept-Language / `en` (same as unauthenticated) |
| zh-Hant matching rule | Any `zh*` language tag (`zh`, `zh-TW`, `zh-HK`, `zh-CN`, `zh-SG`, `zh-Hant`, `zh-Hans`) maps to `zh-Hant` |
| Non-zh non-en tags | Fall back to `en` |
| Cookie update on explicit change | Settings language picker PUT also rewrites `sow_locale` cookie (already does; no change) |
| i18n key parity | Every new key present in both `en` and `zh-Hant` blocks; `bundle()` enforces compile-time parity |
| Interpolation pattern | `${name}`, `${n}`, `${percent}` replaced with `.replace()` at call site (same as v2) |
| Error toast i18n | "Sign out failed" added as a keyed message (`settings.signOut.error`) instead of a hardcoded English string |

---

## Current State (relevant to i18n changes)

| Concern | Location | Status |
|---|---|---|
| Locale resolver | `src/lib/i18n/server.ts:18` | Reads `sow_locale` cookie → authenticated DB setting → `en` fallback. **No `Accept-Language` parsing.** |
| Cookie write | `src/app/api/settings/route.ts` | Already sets `sow_locale` cookie on settings save (language picker). Reuse the same cookie policy. |
| i18n messages | `src/lib/i18n/messages/core.ts` | Has `home.title`, `home.subtitle`, `home.viewSongsets`. Needs new keys for dashboard + landing + sign-out. |
| Bundle type safety | `src/lib/i18n/messages.ts:30` | `bundle()` enforces identical keys across `en` and `zh-Hant`. Missing key = compile error. |
| `t()` lookup | `src/lib/i18n/messages.ts:60` | Pure function `t(locale, key)` used by client components via `useLocale()` hook. |
| Public landing locale | `src/app/page.tsx:8` | Already calls `resolveUserLocale()` — after this change it will get auto-detected locale for free. |

---

## Implementation Plan

### Phase A: Locale Auto-Detection — `src/lib/i18n/server.ts`

**Goal:** Parse `Accept-Language` on the first visit (no cookie, no session) and write the result to the `sow_locale` cookie. Also use it as a fallback for authenticated users who have no saved account locale.

#### A1. Add `Accept-Language` parser

```ts
import { cookies, headers } from "next/headers";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userSettings } from "@/db/schema";
import { eq } from "drizzle-orm";
import { isLocale, Locale, LOCALES } from "./messages";

/**
 * Parse the Accept-Language header and return the best matching locale.
 * - Any `zh*` tag → "zh-Hant"
 * - "en" or no match → "en"
 * - Ignores quality values; first match wins.
 */
function parseAcceptLanguage(headerValue: string | null): Locale {
  if (!headerValue) return "en";
  const tags = headerValue.split(",").map((s) => s.split(";")[0].trim().toLowerCase());
  for (const tag of tags) {
    if (tag.startsWith("zh")) return "zh-Hant";
    if (tag === "en") return "en";
  }
  return "en";
}

/**
 * Write the resolved locale to the sow_locale cookie so subsequent
 * visits (and client-side rehydration) stay consistent.
 */
function setLocaleCookie(locale: Locale) {
  const cookieStore = cookies();
  cookieStore.set("sow_locale", locale, {
    path: "/",
    maxAge: 60 * 60 * 24 * 365, // 365 days
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });
}
```

#### A2. Update `resolveUserLocale()`

```ts
export async function resolveUserLocale(): Promise<Locale> {
  const cookieStore = cookies();
  const cookieLocale = cookieStore.get("sow_locale")?.value;
  const headerValue = (await headers()).get("accept-language");
  const detected = parseAcceptLanguage(headerValue);

  const fallback = (): Locale => (isLocale(cookieLocale) ? cookieLocale : detected);

  try {
    const session = await auth.api.getSession({ headers: await headers() });
    if (!session?.user) {
      // Public page: no cookie yet → detect from header and persist
      if (!isLocale(cookieLocale)) {
        setLocaleCookie(detected);
      }
      return fallback();
    }

    const userId = Number(session.user.id);
    const rows = await db
      .select({ locale: userSettings.locale })
      .from(userSettings)
      .where(eq(userSettings.userId, userId));

    const value = rows[0]?.locale;
    if (isLocale(value)) return value;

    // Authenticated but no saved locale: detect, persist cookie, return detected
    setLocaleCookie(detected);
    return detected;
  } catch {
    return fallback();
  }
}
```

**Rationale:**
- The `Accept-Language` parser is intentionally simple: any `zh*` maps to `zh-Hant` because the app only supports two locales. Quality values are ignored — the first Chinese tag is treated as authoritative.
- The cookie is written **only when there is no valid cookie already**, avoiding unnecessary `Set-Cookie` on every request.
- For authenticated users with no saved setting, the detected locale becomes their effective UI language immediately and is persisted to the cookie. When they later save Settings, the language picker writes `user_settings.locale` and updates the cookie (existing behavior).
- The `secure` flag is environment-gated so local dev (http://localhost) does not drop the cookie.

---

### Phase B: i18n Messages — expand `src/lib/i18n/messages/core.ts`

Add the following keys to **both** the `en` and `zh-Hant` objects. The `bundle()` type system will enforce parity at compile time.

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

### Phase C: Proxy — make `/` public (unchanged from v2)

**File:** `src/proxy.ts:4`

Add `"/"` to `PUBLIC_PATHS`:

```ts
const PUBLIC_PATHS = ["/", "/login", "/register", "/api/auth", "/share", "/api/share"];
```

Rationale: The root page now serves both authenticated (dashboard) and unauthenticated (landing) content. `resolveUserLocale()` handles locale for both cases.

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
  const locale = await resolveUserLocale();  // now includes Accept-Language detection
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    return <PublicLanding locale={locale} />;
  }
  // ... dashboard path
}
```

No code change needed here — `resolveUserLocale()` now handles detection internally. The `locale` prop is passed to both `PublicLanding` and `HomePageClient` so they can call `t(locale, key)`.

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

### Phase I: Tests — i18n assertions

#### I1. `src/test/lib/i18n/server.test.ts` — extend

```ts
describe("resolveUserLocale Accept-Language detection", () => {
  it("detects zh-Hant from Accept-Language 'zh-TW' when no cookie", async () => {
    mockCookie(undefined);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-TW,en-US;q=0.9" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("detects zh-Hant from Accept-Language 'zh' when no cookie", async () => {
    mockCookie(undefined);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh,en;q=0.9" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("falls back to en for unknown language tags", async () => {
    mockCookie(undefined);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "fr-FR,de;q=0.8" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("en");
  });

  it("prefers cookie over Accept-Language", async () => {
    mockCookie("zh-Hant");
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "en-US" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("sets sow_locale cookie when detecting for the first time on a public page", async () => {
    mockCookie(undefined);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-HK" : null),
    } as any);

    await resolveUserLocale();

    const cookieStore = await cookies();
    expect(cookieStore.set).toHaveBeenCalledWith(
      "sow_locale",
      "zh-Hant",
      expect.objectContaining({ path: "/", maxAge: expect.any(Number) })
    );
  });
});
```

#### I2. `src/test/app/home/PublicLanding.test.tsx` — i18n coverage

- Assert that `PublicLanding` renders `t(locale, "home.signedOut.heroTitle")` when `locale="zh-Hant"`.
- Assert that `PublicLanding` renders `t(locale, "home.signedOut.ctaPrimary")` in Traditional Chinese.

#### I3. `src/test/app/home/HomePageClient.test.tsx` — i18n coverage

- Assert greeting uses `home.welcomeBack` with name interpolation.
- Assert stat card labels use `home.stats.*` keys.
- Assert "Favorited by N" badge uses `home.badge.favoritedBy` with `${n}` replaced.

#### I4. `src/test/app/settings-signout.test.tsx` — keyed error toast

- Assert that sign-out failure shows `t("settings.signOut.error")` (not hardcoded English).

#### I5. `src/test/app/header-avatar.test.tsx` — keyed strings

- Assert dropdown shows `t("nav.settings")` and `t("nav.signOut")`.
- Assert signed-out header shows `t("auth.signIn.submit")` and `t("auth.register.submit")`.

#### I6. `src/test/lib/i18n/messages.test.ts` — bundle parity

- Ensure all new keys appear in both `en` and `zh-Hant`. `bundle()` already enforces this at compile time; the test verifies runtime merging produces no blanks.

---

## Files Changed (Summary)

| File | Change |
|------|--------|
| `src/lib/i18n/server.ts` | Add `parseAcceptLanguage()`, `setLocaleCookie()`, update `resolveUserLocale()` to detect and persist browser locale |
| `src/lib/i18n/messages/core.ts` | Add `home.*`, `settings.section.account`, `settings.signOut*`, `nav.signOut` keys (en + zh-Hant) |
| `src/proxy.ts` | Add `"/"` to `PUBLIC_PATHS` (unchanged from v2) |
| `src/components/layout/Header.tsx` | Avatar dropdown + conditional nav links; all strings via `t()`; error toast keyed |
| `src/app/page.tsx` | Branch on session; pass `locale` to `PublicLanding` and `HomePageClient` |
| `src/app/page/PublicLanding.tsx` | **New** — rich marketing landing page; all strings via `t(locale, key)` |
| `src/app/page/HomePageClient.tsx` | **New** — dashboard client component; all strings via `t(locale, key)` |
| `src/components/dashboard/DashboardSongsetCard.tsx` | **New** — lightweight songset card (strings reuse existing keys) |
| `src/components/dashboard/StatCard.tsx` | **New** — icon-enhanced stat card (labels from `home.stats.*`) |
| `src/components/songset/SongCard.tsx` | Add optional `favoriteCount?: number` prop → "Favorited by N" badge (label from `home.badge.favoritedBy`) |
| `src/app/settings/page.tsx` | Add "Account" section with Sign out button; all strings keyed; error toast keyed |
| `src/test/lib/i18n/server.test.ts` | Extend with Accept-Language + cookie persistence tests |
| `src/test/app/home/HomePageClient.test.tsx` | **New** — i18n assertions for dashboard strings |
| `src/test/app/home/PublicLanding.test.tsx` | **New** — i18n assertions for landing page strings |
| `src/test/app/settings-signout.test.tsx` | **New** — sign-out flow + keyed error toast |
| `src/test/app/header-avatar.test.tsx` | **New** — avatar dropdown + nav link i18n |
| `src/test/lib/db/dashboard.test.ts` | **New** — DB helper tests (unchanged from v2) |
| `src/test/accessibility/accessibility.test.tsx` | Extend with home/heading/label/keyboard assertions (unchanged from v2) |

## Out of Scope

- **Multi-locale beyond `en` and `zh-Hant`** — e.g., `zh-Hans`, `ja`, `ko` are out of scope.
- **Quality-value parsing in Accept-Language** — `q=0.9` is ignored; first tag wins. Acceptable for a two-locale app.
- **Per-device locale memory** — cookie is the only persistence mechanism.
- **Client-side `navigator.language` fallback** — server-side `Accept-Language` is sufficient for SSR; client rehydration receives the resolved locale from the server.
- **Android app locale sync** — Android app uses its own locale setting; no coupling to web cookie.
- **All other v2 out-of-scope items** remain unchanged (render_jobs counting, caching community favorites, real screenshots, separate pages, profile page, etc.).

## Verification Commands

```bash
pnpm --filter sow-webapp test
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build
```
