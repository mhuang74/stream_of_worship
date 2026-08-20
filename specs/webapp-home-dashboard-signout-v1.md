# Webapp Home Dashboard & Sign-out v1

## Summary

The `/` route currently renders a static, auth-agnostic landing page (brand title + "View Songsets" button) and there is **no way to sign out** anywhere in the app. This spec transforms `/` into a personalized dashboard for signed-in users and adds sign-out to the Settings page.

For **signed-in users**, the home page shows:
1. A greeting using the user's name ("Welcome back, Michael")
2. Stat cards: songsets created / rendered / shared, favorite songs count, total songs in catalog
3. Most recently touched Songsets (top 3, ordered by `updatedAt`) with limited actions (view, play, share)
4. Favorite Songs (top 4) with favorite-toggle + play-preview
5. A random sampling of other users' favorite songs (top 4) for discovery, shown anonymously with a "Favorited by N" badge

For **signed-out users**, `/` renders a public marketing landing page with Sign in / Register CTAs (instead of the current proxy redirect to `/login`).

Sign-out lives on the **Settings page** as an "Account" section.

## User Decisions

| Decision | Choice |
|----------|--------|
| Sign-out placement | Settings page only (new "Account" section) |
| "Recently touched" definition | `updatedAt` DESC (edits, reorders, renames, re-renders) |
| Recent items count | 3 songsets + 4 favorite songs |
| Other users' favorites selection | Random sample (`ORDER BY random()`) of distinct songs favorited by users other than the current user; excludes songs the current user already favorited |
| Attribution | Anonymous, count only ("Favorited by N people") |
| Stat card scope | User stats (created/rendered/shared/favorites) + global catalog count |
| Landing page item operations | Limited: songsets → view / play / share; songs → open (play preview) / favorite toggle |
| Signed-out home behavior | Make `/` public; show static landing page with Sign in / Register CTAs |

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

Create a new helper module with three functions. All take `userId: number`.

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
home.welcomeBack          "Welcome back, ${name}"            "${name}，歡迎回來"
home.signedOut.title      "Stream of Worship"                "Stream of Worship"
home.signedOut.subtitle   "Worship music transition..."      "敬拜音樂轉場與播放系統..."
home.signedOut.signIn     "Sign in"                          "登入"
home.signedOut.register   "Create account"                   "建立帳號"
home.stats.songsetsCreated   "Songsets created"            "已建立詩歌集"
home.stats.songsetsRendered  "Songsets rendered"           "已渲染詩歌集"
home.stats.songsetsShared    "Songsets shared"             "已分享詩歌集"
home.stats.favoriteSongs     "Favorite songs"              "最愛詩歌"
home.stats.catalogSongs      "Songs in catalog"            "詩歌目錄總數"
home.section.recentSongsets       "Recent songsets"        "最近的詩歌集"
home.section.recentFavorites      "Favorite songs"         "我的最愛"
home.section.communityFavorites   "From the community"     "來自社群的最愛"
home.action.viewAll          "View all"                    "檢視全部"
home.badge.favoritedBy       "Favorited by ${n}"           "${n} 人最愛"
home.empty.recentSongsets       "No songsets yet"         "尚無詩歌集"
home.empty.recentFavorites      "No favorites yet"        "尚無最愛"
home.empty.communityFavorites   "No community favorites"  "尚無社群最愛"
settings.section.account   "Account"                         "帳號"
settings.signOut           "Sign out"                        "登出"
settings.signOut.success   "Signed out"                      "已登出"
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

`isPublicPath` already does exact-match / prefix match, so `"/"` matches only the root. Unauthenticated users now reach `page.tsx`, which branches on session (Phase 5).

---

### Phase 4: Sign-out on Settings page

**File:** `src/app/settings/page.tsx` (already a client component)

#### 4a. Add imports
```ts
import { signOut } from "@/lib/auth-client";
import { LogOut, Loader2 } from "lucide-react";
```

#### 4b. Add sign-out state + handler
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

#### 4c. Render an "Account" section after `SettingsForm`
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

### Phase 5: Home page — server component branches on session

**File:** `src/app/page.tsx`

#### 5a. Imports
```ts
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { getDashboardStats, getRecentSongsets, getRecentFavoriteSongs, getCommunityFavoriteSample } from "@/lib/db/dashboard";
import { HomePageClient } from "./HomePageClient";
import { PublicLanding } from "./PublicLanding";
```

#### 5b. Branch on session
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

#### 5c. `PublicLanding` component — `src/app/page/PublicLanding.tsx`

Small server/presentational component reusing `home.signedOut.*` keys, with `Sign in` → `/login` and `Create account` → `/register` links (`buttonVariants`). No data fetching.

---

### Phase 6: `HomePageClient` component — `src/app/page/HomePageClient.tsx`

New client component. Structure:

```
┌─────────────────────────────────────────────┐
│  "Welcome back, Michael"            [Sign out→ lives on Settings] │
├─────────────────────────────────────────────┤
│  Stats: [Created][Rendered][Shared][Favs][Catalog]  (responsive grid) │
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

#### 6a. Props
```ts
interface HomePageClientProps {
  userName: string;
  stats: { songsetsCreated: number; songsetsRendered: number; songsetsShared: number;
           favoriteSongs: number; catalogSongs: number; };
  recentSongsets: Array<{ id: string; name: string; itemCount: number;
    durationSeconds: number | null; updatedAt: string; renderState: RenderState;
    lastCompletedRenderJobId: string | null; }>;
  recentFavoriteSongs: SongCardData[];
  communityFavorites: Array<SongCardData & { favoriteCount: number }>;
}
```

#### 6b. Handlers (reuse existing hooks)
- `useFavoriteToggle(initialFavoriteIds)` — for both recent favorites (start favorited) and community sample (start not-favorited, since current user hasn't favorited them)
- `useSongPlayback({ resolveSong, ... })` — for play-preview on both song sections
- `useRouter()` — `router.push(`/songsets/${id}/play`)` for songset play
- Share: dynamic-import `ShareDialog` (same as `SongsetsClient.tsx:13-16`), open on share click

#### 6c. Recent Songsets — limited actions
**Do NOT reuse `SongsetRow`** (it renders the full kebab menu). Create a new lightweight **`src/components/dashboard/DashboardSongsetCard.tsx`**:
- Card with: name (Link → `/songsets/[id]`), item count, duration, `updatedAt`, `RenderStatusBadge`
- Inline buttons: **Play** (only if `renderState === "fresh" && lastCompletedRenderJobId`) and **Share**
- No kebab menu, no rename/duplicate/render/download/delete

Trade-off note: a new component duplicates ~40 lines of layout from `SongsetRow`, but honors the "limited actions" decision and keeps the dashboard uncluttered. Alternatively, `SongsetRow` could gain an optional `actions` prop — rejected to avoid risk to the existing `/songsets` page.

#### 6d. Favorite Songs — reuse `SongCard`
Pass `isFavorite`, `onToggleFavorite`, `onPlay`, `isPlaying`, `isPreviewLoading`. No `onAdd`. On toggle-off, remove the song from the local list (mirrors `FavoritesClient.tsx:140-149`).

#### 6e. Community Favorites — reuse `SongCard` + new optional `favoriteCount` prop
Add an **additive, optional** prop to `src/components/songset/SongCard.tsx`:
```ts
favoriteCount?: number;
```
When present, render a small badge near the title: `♥ {favoriteCount}` using `home.badge.favoritedBy` with `${n}` interpolation. Pass `isFavorite={false}`, `onToggleFavorite`, `onPlay`. On toggle-on, flip the heart to filled (the `useFavoriteToggle` set updates).

#### 6f. Empty states
Each section renders its `home.empty.*` message + a CTA link (`/songsets` create, `/favorites` browse, or hides the community section entirely if empty).

#### 6g. "View all" links
`/songsets` and `/favorites` respectively (existing routes).

---

### Phase 7: Tests

#### 7a. `src/test/app/home/HomePageClient.test.tsx` (new)
- Greeting renders `userName` ("Welcome back, Michael")
- All 5 stat cards render with correct labels + values
- Recent songsets section renders 3 `DashboardSongsetCard`s; clicking name navigates to `/songsets/[id]`
- Play button only renders when `renderState === "fresh" && lastCompletedRenderJobId`; clicking calls `router.push` to `/songsets/[id]/play`
- Share button opens `ShareDialog` with correct `songsetId`/`songsetName`
- Favorite songs section renders 4 `SongCard`s with `isFavorite`; toggling off removes the card
- Community section renders 4 `SongCard`s with the "Favorited by N" badge; toggling on flips heart
- Empty states: no songsets → shows `home.empty.recentSongsets`; no favorites → `home.empty.recentFavorites`; no community → section hidden
- "View all" links point to `/songsets` and `/favorites`

#### 7b. `src/test/app/home/PublicLanding.test.tsx` (new)
- Renders brand title + subtitle
- "Sign in" link → `/login`; "Create account" link → `/register`

#### 7c. `src/test/app/settings-signout.test.tsx` (new, or extend `pages.test.tsx`)
- Settings page renders "Account" section with "Sign out" button
- Clicking calls `signOut()` (mock `@/lib/auth-client`) and navigates to `/login`
- Shows "Signed out" toast on success

#### 7d. `src/test/lib/db/dashboard.test.ts` (new, if DB helpers are unit-tested; otherwise integration)
- `getDashboardStats`: correct counts per user (created/rendered/shared/favorites/catalog)
- `getCommunityFavoriteSample`: excludes current user's favorited songs; returns `favoriteCount`; returns ≤ limit
- `getRecentSongsets`: returns top N by `updatedAt`
- `getRecentFavoriteSongs`: returns top N favorited songs

#### 7e. `src/test/accessibility/accessibility.test.tsx` (extend)
- Greeting is an `h1`; section headings are `h2`
- Stat cards have accessible labels
- "Favorited by N" badge is readable by screen readers

---

## Files Changed (Summary)

| File | Change |
|------|--------|
| `src/lib/db/dashboard.ts` | **New** — `getDashboardStats`, `getRecentSongsets`, `getRecentFavoriteSongs`, `getCommunityFavoriteSample` |
| `src/proxy.ts` | Add `"/"` to `PUBLIC_PATHS` |
| `src/lib/i18n/messages/core.ts` | Add `home.*`, `settings.section.account`, `settings.signOut*` keys (en + zh-Hant) |
| `src/app/page.tsx` | Branch on session; fetch dashboard data; render `HomePageClient` or `PublicLanding` |
| `src/app/page/HomePageClient.tsx` | **New** — dashboard client component (greeting, stats, 3 sections, handlers) |
| `src/app/page/PublicLanding.tsx` | **New** — static signed-out landing with Sign in/Register CTAs |
| `src/components/dashboard/DashboardSongsetCard.tsx` | **New** — lightweight songset card (view/play/share only) |
| `src/components/songset/SongCard.tsx` | Add optional `favoriteCount?: number` prop → "Favorited by N" badge |
| `src/app/settings/page.tsx` | Add "Account" section with Sign out button |
| `src/test/app/home/HomePageClient.test.tsx` | **New** tests |
| `src/test/app/home/PublicLanding.test.tsx` | **New** tests |
| `src/test/app/settings-signout.test.tsx` | **New** tests |
| `src/test/lib/db/dashboard.test.ts` | **New** tests |
| `src/test/accessibility/accessibility.test.tsx` | Extend with home/heading/label assertions |

## Out of Scope

- Header user menu / avatar (sign-out is Settings-only per decision)
- Android app changes (uses JSON APIs; no new endpoints required for v1 — dashboard is web-only)
- New API routes (all data fetched server-side via helpers; client mutations reuse existing `/api/favorites`, `/api/share`)
- "Rendered" count via completed `render_jobs` rows (using `songsets.lastCompletedRenderJobId` snapshot instead)
- Caching the community-favorite random sample (re-queried per load; acceptable at scale)
- Personalized/greeting localization beyond the name token
- Reorder/sort controls on dashboard sections

## Verification Commands

```bash
pnpm --filter sow-webapp test
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build
```