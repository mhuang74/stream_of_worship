# Webapp Home Dashboard & Sign-out v2

## Summary

The `/` route currently renders a static, auth-agnostic landing page (brand title + "View Songsets" button) and there is **no way to sign out** anywhere in the app. This spec transforms `/` into a personalized dashboard for signed-in users, a rich marketing landing page for signed-out users, and adds sign-out to both the Settings page and a new header avatar dropdown.

For **signed-in users**, the home page shows:
1. A greeting using the user's name ("Welcome back, Michael")
2. Icon-enhanced stat cards: songsets created / rendered / shared, favorite songs count, total songs in catalog
3. Most recently touched Songsets (top 3, ordered by `updatedAt`) with limited actions (view, play, share)
4. Favorite Songs (top 4) with favorite-toggle + play-preview
5. A random sampling of other users' favorite songs (top 4) for discovery, shown anonymously with a "Favorited by N" badge
6. Quick-create CTAs in empty states ("Create your first songset", "Browse the catalog")

For **signed-out users**, `/` renders a rich public marketing landing page with a hero section, product mockup, feature callouts, how-it-works steps, and Sign in / Register CTAs.

Sign-out lives on the **Settings page** as an "Account" section, and additionally in a **header avatar dropdown** for quick access.

## User Decisions

| Decision | Choice |
|----------|--------|
| Sign-out placement | Settings page ("Account" section) **plus** header avatar dropdown |
| Header avatar | Yes — initials avatar with dropdown: Profile / Settings / Sign out |
| Stat card icons | Icon-enhanced cards with subtle backgrounds; hardcoded lucide icons |
| Stat card responsive grid | 2 cols mobile / 3 cols tablet / 5 cols desktop |
| "Recently touched" definition | `updatedAt` DESC (edits, reorders, renames, re-renders) |
| Recent items count | 3 songsets + 4 favorite songs |
| Other users' favorites selection | Random sample (`ORDER BY random()`) of distinct songs favorited by users other than the current user; excludes songs the current user already favorited |
| Community favorites completion-gate | **Keep existing gate.** Community cards show play-preview (always available). Heart button follows existing `FavoriteButton` logic: disabled + tooltip if user hasn't completed any song; enabled once any song is completed. This preserves intent signaling while allowing discovery playback. |
| Attribution | Anonymous, count only ("Favorited by N people") |
| Stat card scope | User stats (created/rendered/shared/favorites) + global catalog count |
| Landing page item operations | Limited: songsets → view / play / share; songs → play preview / favorite toggle |
| Signed-out home behavior | Make `/` public; show rich marketing landing page with hero, features, how-it-works, Sign in / Register CTAs |
| Public landing screenshot | Static CSS mockup of dashboard embedded in hero (no external image assets) |
| Empty state CTAs | Yes — quick-create CTAs in empty states: "Create your first songset" → /songsets, "Browse the catalog" → /songs |
| Public landing nav links | Yes — header shows Features / How it works / Songs nav links on signed-out landing |

## Current State

| Concern | Location | Status |
|---|---|---|
| Home page | `src/app/page.tsx:7-21` | Static; resolves locale only, never reads session; renders `home.title` + "View Songsets" link |
| Auth access (server) | `auth.api.getSession({ headers: await headers() })` | Inlined per-file; no `auth()` helper |
| Auth access (client) | `useSession`/`signIn`/`signOut` in `src/lib/auth-client.ts:7` | `useSession` unused; `signOut` exported but **never called** |
| Sign-out UI | — | **Does not exist** |
| Proxy | `src/proxy.ts:4` | `PUBLIC_PATHS = ["/login","/register","/api/auth","/share","/api/share"]`; `/` is protected → unauthenticated → redirect to `/login` |
| Header | `src/components/layout/Header.tsx:46` | Brand + 3 nav links; empty `ml-auto` spacer, no user info |
| Songset list data | `listSongsetSummaries(userId, limit, offset, search)` in `src/lib/db/songsets.ts:427` | Orders by `desc(songsets.updatedAt)`, returns `{ songsets, total }` — reusable for "recent" |
| Favorites data | `getFavoriteSongIds(userId)` + `listSongs({ favoritesOnly })` | Reusable for "recent favorites" |
| Other users' favorites | — | **Not exposed**; all favorite queries scoped to current user |
| Stats/aggregate endpoint | — | **None exists** |
| SongsetRow | `src/components/songset/SongsetRow.tsx` | Renders full kebab menu (rename/duplicate/render/play/share/download/delete) — too much for dashboard |
| SongCard | `src/components/songset/SongCard.tsx` | Flexible — only renders buttons for handlers passed in; no "favorited by N" badge yet |
| i18n | `src/lib/i18n/messages/core.ts` | `home.*` keys exist (title/subtitle/viewSongsets); needs new keys; bundle pattern requires en + zh-Hant parity |
| User model | `src/db/schema.ts:122-130` | `name`, `email`, `image`, `createdAt`, `updatedAt` available on session |
| Render tracking | `songsets.lastCompletedRenderJobId` (`schema.ts:193`), `render_jobs.status='completed'` | Queryable for "rendered" count |
| Share tracking | `songset_shares` (`schema.ts:431-444`): `createdByUserId`, `revokedAt`, `expiresAt` | Active-share conditions already defined in `src/app/api/share/route.ts:11-16` |
| Favorites table | `user_favorite_songs` (`schema.ts:381-393`): `userId`, `songId`, `createdAt`, unique(`userId`,`songId`) | Private; needs new cross-user query |
| Catalog | `songs` (`schema.ts:35-71`): `deletedAt` column | Count = `WHERE deletedAt IS NULL` |

### Data-flow convention
Server components call `@/lib/db/*` helpers directly (not `fetch`); client interactions hit `/api/*` routes. Helpers are the single source of query logic shared by both.

---

## Implementation Plan

### Phase 1: DB Layer — new `src/lib/db/dashboard.ts`

Create a new helper module with four functions. All take `userId: number`.

#### 1a. `getDashboardStats(userId)` → aggregate counts

Returns `{ songsetsCreated, songsetsRendered, songsetsShared, favoriteSongs, catalogSongs }`.

```ts
import { db } from "@/db";
import { songsets, songsetShares, userFavorites, songs } from "@/db/schema";
import { eq, isNotNull, isNull, sql, and, or, gt } from "drizzle-orm";

export async function getDashboardStats(userId: number) {
  const [created] = await db.select({ n: sql<number>`count(*)::int` })
    .from(songsets).where(eq(songsets.userId, userId));

  const [rendered] = await db.select({ n: sql<number>`count(*)::int` })
    .from(songsets)
    .where(and(eq(songsets.userId, userId), isNotNull(songsets.lastCompletedRenderJobId)));

  const [shared] = await db.select({ n: sql<number>`count(*)::int` })
    .from(songsetShares)
    .where(and(
      eq(songsetShares.createdByUserId, userId),
      isNull(songsetShares.revokedAt),
      or(isNull(songsetShares.expiresAt), gt(songsetShares.expiresAt, sql`now()`)),
    ));

  const [favs] = await db.select({ n: sql<number>`count(*)::int` })
    .from(userFavorites).where(eq(userFavorites.userId, userId));

  const [catalog] = await db.select({ n: sql<number>`count(*)::int` })
    .from(songs).where(isNull(songs.deletedAt));

  return {
    songsetsCreated: created?.n ?? 0,
    songsetsRendered: rendered?.n ?? 0,
    songsetsShared: shared?.n ?? 0,
    favoriteSongs: favs?.n ?? 0,
    catalogSongs: catalog?.n ?? 0,
  };
}
```

"Rendered" = songsets with at least one successful render (via the snapshot column, uses existing `idx_songsets_user_updated`). Counting completed `render_jobs` rows is an alternative (out of scope).

#### 1b. `getRecentSongsets(userId, limit=3)` → reuse existing helper

```ts
import { listSongsetSummaries } from "./songsets";
export async function getRecentSongsets(userId: number, limit = 3) {
  const { songsets: rows } = await listSongsetSummaries(userId, limit, 0);
  return rows;
}
```

No new query — `listSongsetSummaries` already orders by `updatedAt DESC`.

#### 1c. `getRecentFavoriteSongs(userId, limit=4)` → reuse existing helpers

```ts
import { getFavoriteSongIds } from "./favorites";
import { listSongs } from "./songs";
import { toSongCardData } from "@/lib/song-card-data";
export async function getRecentFavoriteSongs(userId: number, limit = 4) {
  const favoriteSongIds = await getFavoriteSongIds(userId);
  const { songs } = await listSongs(limit, 0, {
    favoriteSongIds,
    favoritesOnly: true,
    visibilityStatus: ["published", "review"],
  });
  return toSongCardData(songs);
}
```

Mirrors `src/app/favorites/page.tsx:25-30`.

#### 1d. `getCommunityFavoriteSample(userId, limit=4)` → new cross-user query

Returns `Array<SongCardData & { favoriteCount: number }>` — distinct songs favorited by **other** users, randomly sampled, excluding songs the current user already favorited, each annotated with its global favorite count.

```ts
export async function getCommunityFavoriteSample(userId: number, limit = 4) {
  // 1. Random sample of distinct song_ids favorited by other users,
  //    excluding the current user's favorites.
  const sampled = await db
    .select({ songId: userFavorites.songId })
    .from(userFavorites)
    .where(
      sql`${userFavorites.songId} NOT IN (
        SELECT song_id FROM user_favorite_songs WHERE user_id = ${userId}
      )`
    )
    .groupBy(userFavorites.songId)
    .orderBy(sql`random()`)
    .limit(limit);

  if (sampled.length === 0) return [];

  const songIds = sampled.map((r) => r.songId);

  // 2. Fetch song rows + recordings (reuse mapSongWithRecordings pattern).
  //    Plus per-song global favorite count.
  // ... join songs ← recordings, where songs.id IN songIds,
  //     and a correlated count subquery on user_favorite_songs.

  return rows.map((r) => ({ ...toSongCardData([r.songs])[0], favoriteCount: r.favoriteCount }));
}
```

**Performance note:** `ORDER BY random()` over `user_favorite_songs` (grouped) is acceptable at expected scale (small worship community). If the table grows, switch to `TABLESAMPLE` or a pre-aggregated "popular favorites" cache. Out of scope for v1.

**Privacy:** No user names/ids/avatars are returned — only song data + an aggregate count.

---

### Phase 2: i18n — new keys in `src/lib/i18n/messages/core.ts`

Add to both `en` and `zh-Hant` blocks (parity required by the `bundle()` type):

```
home.welcomeBack                    "Welcome back, ${name}"            "${name}，歡迎回來"
home.signedOut.title                "Stream of Worship"                "Stream of Worship"
home.signedOut.subtitle             "Worship music transition and playback system. Manage songsets, render audio and video, and lead worship seamlessly."            "敬拜音樂轉場與播放系統。管理詩歌集、渲染音訊與影片，無縫帶領敬拜。"
home.signedOut.heroTag              "Seamless worship music transitions"            "無縫敬拜音樂轉場"
home.signedOut.heroTitle            "Lead worship without awkward pauses."            "帶領敬拜，無需尷尬停頓。"
home.signedOut.heroDescription      "Stream of Worship analyzes tempo, key, and structure to generate smooth transitions between songs — then renders audio and lyrics videos for your service."            "Stream of Worship 分析速度、調性與結構，生成歌曲間的流暢轉場——並為您的服事渲染音訊與歌詞影片。"
home.signedOut.ctaPrimary           "Get started free"                   "免費開始使用"
home.signedOut.ctaSecondary         "Sign in"                            "登入"
home.signedOut.ctaFooter            "No credit card required · Free for personal use"            "無需信用卡 · 個人使用免費"
home.signedOut.featuresTitle        "Everything you need for a seamless service"            "無縫服事所需的一切"
home.signedOut.featuresDescription  "From song selection to rendered video, Stream of Worship handles the technical details so you can focus on leading."            "從選歌到渲染影片，Stream of Worship 處理技術細節，讓您專注帶領。"
home.signedOut.feature.build        "Build songsets"                     "建立詩歌集"
home.signedOut.feature.buildDesc    "Curate songs from a catalog of 300+ worship songs. Reorder, adjust keys, and preview transitions before you commit."            "從 300+ 首敬拜詩歌曲目中精選。重新排序、調整調性、預覽轉場。"
home.signedOut.feature.render       "Render audio & video"               "渲染音訊與影片"
home.signedOut.feature.renderDesc   "Generate a single audio file with smooth crossfades, plus a lyrics video with your choice of template, resolution, and fonts."            "生成帶有流暢交叉淡入淡出的單一音訊檔案，以及可選擇模板、解析度與字體的歌詞影片。"
home.signedOut.feature.share        "Share with your team"               "與團隊分享"
home.signedOut.feature.shareDesc    "Share rendered songsets with your worship team via a link. No accounts needed for viewers."            "透過連結分享渲染後的詩歌集給敬拜團隊。觀看者無需帳號。"
home.signedOut.howItWorksTitle      "How it works"                       "運作方式"
home.signedOut.step1                "Pick your songs"                    "選擇詩歌"
home.signedOut.step1Desc            "Browse the catalog and add songs to a songset."            "瀏覽曲目並加入詩歌集。"
home.signedOut.step2                "Tune transitions"                   "調整轉場"
home.signedOut.step2Desc            "Adjust gap beats, key shifts, and preview each transition."            "調整間隔拍數、調性變化，並預覽每個轉場。"
home.signedOut.step3                "Render"                             "渲染"
home.signedOut.step3Desc            "Generate the audio mix and lyrics video in the cloud."            "在雲端生成音訊混音與歌詞影片。"
home.signedOut.step4                "Lead & share"                       "帶領與分享"
home.signedOut.step4Desc            "Play from any device or share a link with your team."            "在任何裝置播放，或分享連結給團隊。"
home.signedOut.ctaBottomTitle       "Ready to lead worship seamlessly?"  "準備好無縫帶領敬拜了嗎？"
home.signedOut.ctaBottomDesc        "Join the community of worship leaders using Stream of Worship."            "加入使用 Stream of Worship 的敬拜帶領者社群。"
home.signedOut.ctaBottomPrimary     "Create your free account"           "建立免費帳號"
home.signedOut.nav.features         "Features"                           "功能"
home.signedOut.nav.howItWorks       "How it works"                       "運作方式"
home.signedOut.nav.songs            "Songs"                              "詩歌"

home.stats.songsetsCreated          "Songsets created"                   "已建立詩歌集"
home.stats.songsetsRendered         "Songsets rendered"                  "已渲染詩歌集"
home.stats.songsetsShared           "Songsets shared"                    "已分享詩歌集"
home.stats.favoriteSongs            "Favorite songs"                     "最愛詩歌"
home.stats.catalogSongs             "Songs in catalog"                   "詩歌目錄總數"
home.section.recentSongsets         "Recent songsets"                    "最近的詩歌集"
home.section.recentFavorites        "Favorite songs"                     "我的最愛"
home.section.communityFavorites     "From the community"                 "來自社群的最愛"
home.action.viewAll                 "View all"                           "檢視全部"
home.badge.favoritedBy              "Favorited by ${n}"                  "${n} 人最愛"
home.empty.recentSongsets           "No songsets yet"                    "尚無詩歌集"
home.empty.recentFavorites          "No favorites yet"                   "尚無最愛"
home.empty.communityFavorites       "No community favorites"             "尚無社群最愛"
home.empty.createSongset            "Create your first songset"          "建立您的第一個詩歌集"
home.empty.browseCatalog            "Browse the catalog"                 "瀏覽曲目"

settings.section.account            "Account"                            "帳號"
settings.signOut                      "Sign out"                           "登出"
settings.signOut.success            "Signed out"                         "已登出"

nav.signOut                         "Sign out"                           "登出"
nav.settings                        "Settings"                           "設定"
```

Reuse existing keys where possible: `songsets.action.play`, `songsets.action.share`, `favorites.empty.*`.

`${name}` / `${n}` interpolation follows the existing `favorites.empty.description` `${percent}` pattern (string `.Replace()` at call site).

---

### Phase 3: Proxy — make `/` public

**File:** `src/proxy.ts:4`

Add `"/"` to `PUBLIC_PATHS`:

```ts
const PUBLIC_PATHS = ["/", "/login", "/register", "/api/auth", "/share", "/api/share"];
```

`isPublicPath` already does exact-match / prefix match, so `"/"` matches only the root. Unauthenticated users now reach `page.tsx`, which branches on session (Phase 7).

---

### Phase 4: Header — avatar dropdown with user info

**File:** `src/components/layout/Header.tsx`

#### 4a. Add imports
```ts
import { useSession } from "@/lib/auth-client";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { User, Settings, LogOut } from "lucide-react";
```

#### 4b. Fetch session
```ts
const { data: session } = useSession();
const user = session?.user;
```

#### 4c. Replace `ml-auto` spacer with avatar + dropdown
```tsx
<div className="ml-auto flex items-center gap-2">
  {user ? (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="rounded-full size-8">
          <Avatar className="size-8">
            <AvatarFallback className="bg-primary text-primary-foreground text-xs font-semibold">
              {user.name?.charAt(0).toUpperCase() ?? "?"}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem disabled className="text-xs text-muted-foreground">
          <User className="size-4 mr-2" />
          {user.name}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => router.push("/settings")}>
          <Settings className="size-4 mr-2" />
          {t("nav.settings")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleSignOut}>
          <LogOut className="size-4 mr-2" />
          {t("nav.signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ) : (
    <>
      <Link href="/login" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
        {t("auth.signIn.submit")}
      </Link>
      <Link href="/register" className={cn(buttonVariants({ size: "sm" }))}>
        {t("auth.register.submit")}
      </Link>
    </>
  )}
</div>
```

#### 4d. Conditionally render nav links based on auth state
On signed-out landing, show `Features`, `How it works`, `Songs` links (anchor scroll or separate pages; for v2, use `#features`, `#how-it-works` anchors on the same page). On signed-in, keep existing nav links (`Songsets`, `Favorites`, `Settings`).

#### 4e. Sign-out handler
```ts
import { signOut } from "@/lib/auth-client";
import { toast } from "sonner";

async function handleSignOut() {
  try {
    await signOut();
    toast.success(t("settings.signOut.success"));
    router.push("/login");
    router.refresh();
  } catch {
    toast.error("Sign out failed");
  }
}
```

Rationale: the avatar dropdown provides quick sign-out access without bloating the header. Sign-out remains on Settings too for users who prefer navigating there.

---

### Phase 5: Sign-out on Settings page

**File:** `src/app/settings/page.tsx` (already a client component)

#### 5a. Add imports
```ts
import { signOut } from "@/lib/auth-client";
import { LogOut, Loader2 } from "lucide-react";
```

#### 5b. Add sign-out state + handler
```ts
const [isSigningOut, setIsSigningOut] = useState(false);

async function handleSignOut() {
  setIsSigningOut(true);
  try {
    await signOut();
    toast.success(t("settings.signOut.success"));
    router.push("/login");
    router.refresh();
  } catch {
    toast.error("Sign out failed");
  } finally {
    setIsSigningOut(false);
  }
}
```

#### 5c. Render an "Account" section after `SettingsForm`
```tsx
{settings && !isLoading && (
  <>
    <SettingsForm ... />
    <div className="mt-8 border-t pt-6">
      <h2 className="text-lg font-semibold mb-3">{t("settings.section.account")}</h2>
      <Button variant="outline" onClick={handleSignOut} disabled={isSigningOut}>
        {isSigningOut ? <Loader2 className="size-4 mr-2 animate-spin" /> : <LogOut className="size-4 mr-2" />}
        {t("settings.signOut")}
      </Button>
    </div>
  </>
)}
```

Rationale: keeps `SettingsForm` focused on settings CRUD; sign-out is an account action, not a setting. Redirects to `/login` so another user can sign in on the same machine (matches the requirement).

---

### Phase 6: Public Landing — rich marketing page

**File:** `src/app/page/PublicLanding.tsx`

New server/presentational component. Structure follows the prototype:

```
┌─────────────────────────────────────────────┐
│  Header: Brand + [Features] [How it works] [Songs]  [Sign in] [Register]  │
├─────────────────────────────────────────────┤
│  Hero (gradient bg):                                                        │
│  ✦ Tagline · Title · Description · [Get started free] [Sign in] · footer    │
│  ┌─────────────────────────────────────────┐                              │
│  │  CSS mockup of dashboard screenshot       │  ← embedded static visual    │
│  └─────────────────────────────────────────┘                              │
├─────────────────────────────────────────────┤
│  Features (3 cards): Build / Render / Share                                 │
├─────────────────────────────────────────────┤
│  How it works (4 numbered steps): Pick → Tune → Render → Lead           │
├─────────────────────────────────────────────┤
│  Bottom CTA: Title · Description · [Create account] [Sign in]             │
├─────────────────────────────────────────────┤
│  Footer: Brand · ©                                                        │
└─────────────────────────────────────────────┘
```

#### 6a. Hero section
- Background: subtle gradient using CSS `linear-gradient` with muted cool tones (no external image assets)
- Tagline: pill badge with `✦` prefix — "Seamless worship music transitions"
- Title: "Lead worship without awkward pauses." with a gradient-text span on "awkward pauses."
- Description: one-paragraph value prop
- CTAs: primary "Get started free" → `/register`, secondary "Sign in" → `/login`
- Footer note: "No credit card required · Free for personal use"
- Right column: static CSS mockup of the dashboard (no image assets). Reuses card/border/bg utilities to approximate the dashboard screenshot. This is self-contained HTML/CSS, no external dependencies.

#### 6b. Features section
- 3-column grid on desktop, 1-column on mobile
- Each card: emoji icon placeholder (🎼 🎬 🔗) in a `size-10 rounded-lg bg-muted` container, title, description
- Cards have `hover:shadow-md` transition

#### 6c. How it works section
- Numbered steps (1–4) in a 4-column grid
- Each step: circular number badge (`bg-primary text-primary-foreground`), title, description
- Background: `bg-muted/50 border-y border-border` to separate from surrounding sections

#### 6d. Bottom CTA section
- Centered text with primary and secondary buttons

#### 6e. Footer
- Brand name left, copyright right
- `border-t border-border`

#### 6f. Responsive behavior
- Mobile: single column, stacked sections, full-width CTAs
- Tablet: 2-column features, 2-column how-it-works
- Desktop: full layout as described

---

### Phase 7: Home page — server component branches on session

**File:** `src/app/page.tsx`

#### 7a. Imports
```ts
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { getDashboardStats, getRecentSongsets, getRecentFavoriteSongs, getCommunityFavoriteSample } from "@/lib/db/dashboard";
import { HomePageClient } from "./page/HomePageClient";
import { PublicLanding } from "./page/PublicLanding";
```

#### 7b. Branch on session
```ts
export default async function HomePage() {
  const locale = await resolveUserLocale();
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    return <PublicLanding locale={locale} />;
  }

  const userId = Number(session.user.id);
  const [stats, recentSongsets, recentFavoriteSongs, communityFavorites] = await Promise.all([
    getDashboardStats(userId),
    getRecentSongsets(userId, 3),
    getRecentFavoriteSongs(userId, 4),
    getCommunityFavoriteSample(userId, 4),
  ]);

  return (
    <HomePageClient
      userName={session.user.name}
      stats={stats}
      recentSongsets={recentSongsets.map(serializeSongset)}
      recentFavoriteSongs={recentFavoriteSongs}
      communityFavorites={communityFavorites}
    />
  );
}
```

Serialize dates to ISO strings for the client component (same pattern as `songsets/page.tsx`). `Promise.all` parallelizes the four queries.

---

### Phase 8: `HomePageClient` component — `src/app/page/HomePageClient.tsx`

New client component. Structure follows the prototype:

```
┌─────────────────────────────────────────────┐
│  "Welcome back, Michael"                                         [Avatar →]  │
├─────────────────────────────────────────────┤
│  Stats: [Created🎼][Rendered🎬][Shared🔗][Favs❤️][Catalog📚]  (2/3/5 grid)    │
├─────────────────────────────────────────────┤
│  Recent songsets              [View all →]   │
│  [DashboardSongsetCard]×3                    │  ← view (link) / play / share
├─────────────────────────────────────────────┤
│  Favorite songs               [View all →]   │
│  [SongCard]×4                                │  ← favorite toggle / play preview
├─────────────────────────────────────────────┤
│  From the community                          │
│  [SongCard + ♥ N]×4                          │  ← favorite toggle / play preview
└─────────────────────────────────────────────┘
```

#### 8a. Props
```ts
interface HomePageClientProps {
  userName: string;
  stats: { songsetsCreated: number; songsetsRendered: number; songsetsShared: number;
           favoriteSongs: number; catalogSongs: number; };
  recentSongsets: Array<{ id: string; name: string; itemCount: number;
    durationSeconds: number | null; updatedAt: string; renderState: RenderState;
    lastCompletedRenderJobId: string | null; };
  recentFavoriteSongs: SongCardData[];
  communityFavorites: Array<SongCardData & { favoriteCount: number }>;
}
```

#### 8b. Handlers (reuse existing hooks)
- `useFavoriteToggle(initialFavoriteIds)` — for both recent favorites (start favorited) and community sample (start not-favorited, since current user hasn't favorited them)
- `useSongPlayback({ resolveSong, ... })` — for play-preview on both song sections
- `useRouter()` — `router.push(`/songsets/${id}/play`)` for songset play
- Share: dynamic-import `ShareDialog` (same as `SongsetsClient.tsx:13-16`), open on share click

#### 8c. Stat cards — icon-enhanced with hover lift
**File:** `src/components/dashboard/StatCard.tsx` (new, or inline in HomePageClient)

Each stat card:
- `rounded-xl border border-border bg-card p-4`
- `hover:shadow-sm hover:-translate-y-px` transition
- Icon in a `size-7 rounded-md bg-muted` container (hardcoded lucide icons):
  - `songsetsCreated` → `FileMusic` 🎼
  - `songsetsRendered` → `Video` 🎬
  - `songsetsShared` → `Share2` 🔗
  - `favoriteSongs` → `Heart` ❤️
  - `catalogSongs` → `Library` 📚
- Label: `text-xs text-muted-foreground`
- Value: `text-2xl font-bold`
- Responsive grid: `grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3`
- `aria-label="Your stats"` on the section

#### 8d. Recent Songsets — limited actions
**File:** `src/components/dashboard/DashboardSongsetCard.tsx` (new)

- Card: `rounded-xl border border-border bg-card p-4 hover:shadow-md transition-shadow`
- Name: Link → `/songsets/[id]`, `font-medium text-sm truncate`
- Render status badge: inline (Fresh/Draft/Queued/etc.)
- Meta row: item count · duration · updatedAt, `text-xs text-muted-foreground`
- Action row (mt-3):
  - **Play** button: `variant="default" size="sm"`, only renders when `renderState === "fresh" && lastCompletedRenderJobId`
  - **Share** button: `variant="outline" size="sm"`, always renders
- No kebab menu, no rename/duplicate/render/download/delete

Trade-off note: a new component duplicates ~40 lines of layout from `SongsetRow`, but honors the "limited actions" decision and keeps the dashboard uncluttered. Alternatively, `SongsetRow` could gain an optional `actions` prop — rejected to avoid risk to the existing `/songsets` page.

#### 8e. Favorite Songs — reuse `SongCard`
Pass `isFavorite`, `onToggleFavorite`, `onPlay`, `isPlaying`, `isPreviewLoading`. No `onAdd`. On toggle-off, remove the song from the local list (mirrors `FavoritesClient.tsx:140-149`).

#### 8f. Community Favorites — reuse `SongCard` + new optional `favoriteCount` prop
Add an **additive, optional** prop to `src/components/songset/SongCard.tsx`:
```ts
favoriteCount?: number;
```
When present, render a small badge near the title: inline-flex `♥ {favoriteCount}` using `home.badge.favoritedBy` with `${n}` interpolation. Badge style: `rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground`.

Pass `isFavorite={false}`, `onToggleFavorite`, `onPlay`. On toggle-on, flip the heart to filled (the `useFavoriteToggle` set updates).

**Completion-gate behavior**: `FavoriteButton` on community cards follows existing logic exactly. The heart is disabled with a tooltip if the user has never completed a song. Play-preview is always available (same as any SongCard). This preserves intent signaling while allowing discovery playback.

#### 8g. Empty states
Each section renders its `home.empty.*` message + a CTA link:
- No songsets: `home.empty.recentSongsets` + "Create your first songset" → `/songsets` (button `variant="outline"`)
- No favorites: `home.empty.recentFavorites` + "Browse the catalog" → `/songs` (button `variant="outline"`)
- No community favorites: **hide the section entirely** (per v1 spec). Optional enhancement: show a one-line message "Community favorites appear when more users favorite songs you haven't heard." — out of scope for v2.

#### 8h. "View all" links
`/songsets` and `/favorites` respectively (existing routes). Style: `text-sm text-primary hover:underline`.

---

### Phase 9: Tests

#### 9a. `src/test/app/home/HomePageClient.test.tsx` (new)
- Greeting renders `userName` ("Welcome back, Michael")
- All 5 stat cards render with correct icons, labels, and values
- Stat cards have hover/transition classes
- Recent songsets section renders 3 `DashboardSongsetCard`s; clicking name navigates to `/songsets/[id]`
- Play button only renders when `renderState === "fresh" && lastCompletedRenderJobId`; clicking calls `router.push` to `/songsets/[id]/play`
- Share button opens `ShareDialog` with correct `songsetId`/`songsetName`
- Favorite songs section renders 4 `SongCard`s with `isFavorite`; toggling off removes the card
- Community section renders 4 `SongCard`s with the "Favorited by N" badge; toggling on flips heart
- Empty states: no songsets → shows `home.empty.recentSongsets` + "Create your first songset" CTA; no favorites → `home.empty.recentFavorites` + "Browse the catalog" CTA; no community → section hidden
- "View all" links point to `/songsets` and `/favorites`

#### 9b. `src/test/app/home/PublicLanding.test.tsx` (new)
- Renders hero title, subtitle, tagline badge, primary CTA → `/register`, secondary CTA → `/login`
- Renders 3 feature cards with icons, titles, descriptions
- Renders 4 "How it works" steps with numbered badges
- Renders bottom CTA section
- Renders footer with brand and copyright
- Header shows nav links (Features, How it works, Songs) and auth buttons

#### 9c. `src/test/app/settings-signout.test.tsx` (new, or extend `pages.test.tsx`)
- Settings page renders "Account" section with "Sign out" button
- Clicking calls `signOut()` (mock `@/lib/auth-client`) and navigates to `/login`
- Shows "Signed out" toast on success

#### 9d. `src/test/app/header-avatar.test.tsx` (new)
- Header shows avatar with user initial when signed in
- Avatar dropdown shows user name, Settings link, Sign out button
- Clicking Settings navigates to `/settings`
- Clicking Sign out calls `signOut()` and navigates to `/login`
- Header shows Sign in / Register buttons when signed out

#### 9e. `src/test/lib/db/dashboard.test.ts` (new, if DB helpers are unit-tested; otherwise integration)
- `getDashboardStats`: correct counts per user (created/rendered/shared/favorites/catalog)
- `getCommunityFavoriteSample`: excludes current user's favorited songs; returns `favoriteCount`; returns ≤ limit
- `getRecentSongsets`: returns top N by `updatedAt`
- `getRecentFavoriteSongs`: returns top N favorited songs

#### 9f. `src/test/accessibility/accessibility.test.tsx` (extend)
- Greeting is an `h1`; section headings are `h2`
- Stat cards have accessible labels
- "Favorited by N" badge is readable by screen readers
- Header avatar dropdown is keyboard-navigable

---

## Files Changed (Summary)

| File | Change |
|------|--------|
| `src/lib/db/dashboard.ts` | **New** — `getDashboardStats`, `getRecentSongsets`, `getRecentFavoriteSongs`, `getCommunityFavoriteSample` |
| `src/proxy.ts` | Add `"/"` to `PUBLIC_PATHS` |
| `src/lib/i18n/messages/core.ts` | Add `home.*`, `settings.section.account`, `settings.signOut*`, `nav.signOut` keys (en + zh-Hant) |
| `src/components/layout/Header.tsx` | Add avatar dropdown with user initial, Settings link, Sign out; conditional nav links (auth vs. landing); Sign in / Register buttons when signed out |
| `src/app/page.tsx` | Branch on session; fetch dashboard data; render `HomePageClient` or `PublicLanding` |
| `src/app/page/HomePageClient.tsx` | **New** — dashboard client component (greeting, icon-enhanced stats, 3 sections, handlers) |
| `src/app/page/PublicLanding.tsx` | **New** — rich marketing landing page (hero with CSS mockup, features, how-it-works, CTAs, footer) |
| `src/components/dashboard/DashboardSongsetCard.tsx` | **New** — lightweight songset card (view/play/share only) |
| `src/components/dashboard/StatCard.tsx` | **New** — icon-enhanced stat card with hover lift |
| `src/components/songset/SongCard.tsx` | Add optional `favoriteCount?: number` prop → "Favorited by N" badge |
| `src/app/settings/page.tsx` | Add "Account" section with Sign out button |
| `src/test/app/home/HomePageClient.test.tsx` | **New** tests |
| `src/test/app/home/PublicLanding.test.tsx` | **New** tests |
| `src/test/app/settings-signout.test.tsx` | **New** tests |
| `src/test/app/header-avatar.test.tsx` | **New** tests |
| `src/test/lib/db/dashboard.test.ts` | **New** tests |
| `src/test/accessibility/accessibility.test.tsx` | Extend with home/heading/label/keyboard assertions |

## Out of Scope

- **Android app changes** (uses JSON APIs; no new endpoints required for v2 — dashboard is web-only)
- **New API routes** (all data fetched server-side via helpers; client mutations reuse existing `/api/favorites`, `/api/share`)
- **"Rendered" count via completed `render_jobs` rows** (using `songsets.lastCompletedRenderJobId` snapshot instead)
- **Caching the community-favorite random sample** (re-queried per load; acceptable at scale)
- **Personalized/greeting localization beyond the name token**
- **Reorder/sort controls on dashboard sections**
- **Real product screenshots on landing page** (using self-contained CSS mockup instead)
- **Separate Features / How it works / Songs pages** (anchor scroll on same page for v2)
- **User profile page** (avatar dropdown shows name + Settings link, but no `/profile` route)

## Verification Commands

```bash
pnpm --filter sow-webapp test
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build
```
