# Fix Favorites Page: Empty-Favorites Filter & Pagination Spinner — v1

## Summary

Two bugs in the `/favorites` page (`delivery/webapp`):

1. **All songs show as favorites.** The logged-in user has **0 favorites** in `user_favorite_songs`, yet the page lists the entire catalog (438 songs) with every heart filled.
2. **Pagination beyond page 1 is stuck on a spinner** (reproduced 40s+; the network completes in 1–2s, so it is a client-side loading-state bug, not server slowness).

## Root Cause Analysis

### Bug 1 — Empty favorites ignored (`favoritesOnly` filter is a no-op)
- `favoritesOnlyPredicate()` in `src/lib/db/favorites.ts:54` returns `undefined` when `favoriteSongIds` is empty:
  ```ts
  if (!favoriteSongIds || favoriteSongIds.length === 0) return undefined;
  ```
- In `listSongs()` (`songs.ts:302`) the favorites clause becomes `undefined`, so `listWhereClause = whereClause` — the filter is dropped and **all** songs are returned.
- `FavoritesClient.tsx:156` hardcodes `isFavorite` on every card, so all 438 appear as favorites instead of the intended "No favorites yet" empty state (`FavoritesClient.tsx:119`).
- Same helper is used in `search.ts:137`, so the same latent bug affects favorites-only search.

### Bug 2 — Pagination spinner stuck (client race condition)
- `FavoritesClient.tsx:42-75` fetch effect depends on `[page, pageSize, currentPage, initialSongs.length]` and has an SSR-skip early-return.
- Clicking a page does **two** things: `setPage(2)` (starts a client fetch) **and** `router.replace('/favorites?page=2')` (line 78), which triggers an RSC navigation that changes the `currentPage` prop.
- Because `currentPage` is in the effect deps, the effect re-runs → its cleanup sets `cancelled=true` → the in-flight fetch's `finally` (`setIsLoading(false)`) is skipped → the new effect early-returns (`page === currentPage`) **without** resetting `isLoading`.
- Result: `isLoading` stuck `true` → infinite spinner, list never renders.
- The **working** `SongsetsClient` fetch effect (`SongsetsClient.tsx:82-130`) deps are `[page, committedSearch, pageSize, refreshKey]` — no `currentPage`, no SSR-skip guard — which is why songset pagination works. Favorites diverged from this pattern.

---

## Fix Plan

### Phase 1 — DB layer: empty favorites must yield zero results

**File:** `delivery/webapp/src/lib/db/favorites.ts`

Modify `favoritesOnlyPredicate` so an empty favorite list means "match nothing" instead of "no filter":

```ts
export function favoritesOnlyPredicate(
  favoriteSongIds: string[] | undefined
): SQL | undefined {
  if (!favoriteSongIds) return undefined; // context not loaded → no-op
  if (favoriteSongIds.length === 0) return sql`false`; // favorites-only, no favorites → match nothing
  return sql`${songs.id} = ANY(${favoriteSongIds})`;
}
```

Effect: `/favorites` returns `{ songs: [], total: 0 }` → `FavoritesClient` renders the existing "No favorites yet" empty state. Also fixes favorites-only search (`search.ts:137`).

### Phase 2 — Client: fix stuck spinner, align with SongsetsClient pattern

**File:** `delivery/webapp/src/app/favorites/FavoritesClient.tsx`

Replace the fetch effect (lines 42–75) with a mount-once guard + deps scoped to `[page, pageSize]`:

```tsx
const skipInitialFetchRef = useRef(true);

useEffect(() => {
  if (skipInitialFetchRef.current) {
    skipInitialFetchRef.current = false;
    return; // don't refetch SSR page 1 on mount
  }
  let cancelled = false;
  async function loadPage() {
    setIsLoading(true);
    try {
      const offset = (page - 1) * pageSize;
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String(offset),
        favoritesOnly: "1",
        visibilityStatus: "published,review",
      });
      const res = await fetch(`/api/songs?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load favorites");
      const data = await res.json();
      if (cancelled) return;
      setSongs(toSongCardData(data.songs));
      setTotal(data.total);
    } catch {
      if (!cancelled) toast.error("Failed to load favorites");
    } finally {
      if (!cancelled) setIsLoading(false);
    }
  }
  loadPage();
  return () => { cancelled = true; };
}, [page, pageSize]);
```

- Add `useRef` to the React import (line 3).
- Keep the URL-sync effect (78–83), the fallback-to-page-1 effect (97–102), and the empty-state render unchanged.
- Why it works: RSC navigation changes `currentPage`/`initialSongs`, but neither is in the deps, so the effect no longer re-runs mid-flight and never cancels the in-flight fetch. `isLoading` always clears in `finally`.

---

## Tests

**`delivery/webapp/src/test/lib/db/songs.test.ts`**
- `listSongs(…, { favoriteSongIds: [], favoritesOnly: true })` returns 0 songs (empty favorites → empty result).

**New/updated `delivery/webapp/src/test/app/favorites/FavoritesClient.test.tsx`**
- Empty favorites: renders "No favorites yet" when `initialSongs=[]`, `initialTotal=0`.
- After clicking page 2, assert the fetched songs render (not stuck spinner) and `isLoading` clears — current test only asserts fetch URL + `router.replace`.
- Repeated page clicks don't leave the loader stuck.

## Files Changed

| File | Change |
|------|--------|
| `delivery/webapp/src/lib/db/favorites.ts` | `favoritesOnlyPredicate` returns `sql`false`` for empty array |
| `delivery/webapp/src/app/favorites/FavoritesClient.tsx` | Fetch effect deps → `[page, pageSize]` + mount-once ref guard |
| `delivery/webapp/src/test/lib/db/songs.test.ts` | Empty-favorites filter test |
| `delivery/webapp/src/test/app/favorites/FavoritesClient.test.tsx` | Empty-state + "renders after page change" tests |

## Verification (manual, Chrome DevTools)

1. Log in as the user with 0 favorites → `/favorites` shows "No favorites yet" (not 438 songs).
2. To test pagination, seed `>20` rows into `user_favorite_songs` (dev DB only — favoriting in-app requires the ≥90% hearing gate / ADR-0002, so direct seeding is the practical path). Then click page 2/3 → songs render immediately, no spinner.
3. `pnpm test`, `pnpm lint`, `pnpm build` in `delivery/webapp`.

## Out of Scope
- Changing the ≥90% favoriting gate / ADR-0002.
- The count discrepancy (483 vs 438) — 438 is just songs with `published`/`review` recordings; unrelated to the filter bug.
- Other paginated pages (songsets already follow the correct pattern).
